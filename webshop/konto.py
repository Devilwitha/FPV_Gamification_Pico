"""Konto-Bereich: Profil und Benutzername bearbeiten, Passwort aendern und
"Meine Copter" verwalten (/account/profil, /account/copter).

Nur fuer echte Logins (session["account_email"] mit Konto in db.accounts) -
die implizite Anmeldung ueber eine Checkout-Session reicht hier nicht.
Die E-Mail-Adresse ist bewusst nicht aenderbar: an ihr haengen Bestellungen,
Lizenzen und die Server-Ablage der Drohnen Tools.
"""

import re
from functools import wraps

from flask import Blueprint, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db

USERNAME_RE = re.compile(r"^[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß_.-]{2,23}$")
RESERVED_USERNAMES = {"admin", "administrator", "bollisoft", "moderator", "support", "system"}
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

bp = Blueprint("konto", __name__, url_prefix="/account")


def validate_username(username, own_email=None):
    """Fehlermeldung oder None. own_email: eigenes Konto (darf den Namen behalten)."""
    if not username:
        return "Bitte einen Benutzernamen angeben."
    if not USERNAME_RE.match(username):
        return ("Der Benutzername muss 3 bis 24 Zeichen lang sein und darf nur Buchstaben, "
                "Ziffern sowie _ . - enthalten (am Anfang ein Buchstabe oder eine Ziffer).")
    if username.lower() in RESERVED_USERNAMES:
        return "Dieser Benutzername ist reserviert."
    existing = db.get_account_by_username(username)
    if existing is not None and (own_email is None or existing["email"].lower() != own_email.lower()):
        return "Dieser Benutzername ist bereits vergeben."
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
    profile_error = password_error = None
    message = request.args.get("gespeichert")
    values = dict(account)
    if request.method == "POST" and request.form.get("action") == "profile":
        values = {key: (request.form.get(key) or "").strip() for key in ("username", "full_name", "country", "address", "phone")}
        profile_error = validate_username(values["username"], own_email=account["email"])
        if profile_error is None and (not values["full_name"] or not values["country"]):
            profile_error = "Bitte Name und Land ausfüllen."
        if profile_error is None and any(len(values[key]) > 200 for key in values):
            profile_error = "Eine Angabe ist zu lang (höchstens 200 Zeichen)."
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
            password_error = "Das aktuelle Passwort ist falsch."
        elif len(new) < 8:
            password_error = "Das neue Passwort muss mindestens 8 Zeichen lang sein."
        elif new != repeat:
            password_error = "Die neuen Passwörter stimmen nicht überein."
        elif new == current:
            password_error = "Das neue Passwort muss sich vom aktuellen unterscheiden."
        else:
            db.update_account_password(account["email"], generate_password_hash(new))
            return redirect(url_for("konto.profile", gespeichert="passwort"))
    return render_template(
        "account_profile.html",
        account=account,
        values=values,
        profile_error=profile_error,
        password_error=password_error,
        message=message,
        next_url=request.args.get("next") if (request.args.get("next") or "").startswith("/forum") else None,
    )


def _copter_fields(form):
    """(felder, fehler) aus dem Formular."""
    fields = {key: (form.get(key) or "").strip()[:limit] for key, limit in COPTER_TEXT_FIELDS.items()}
    fields["category"] = form.get("category") if form.get("category") in COPTER_CATEGORIES else "Sonstiges"
    weight = (form.get("weight_g") or "").strip()
    fields["weight_g"] = None
    if not fields["name"]:
        return fields, "Bitte einen Namen für den Copter angeben."
    if weight:
        if not weight.isdigit() or not 1 <= int(weight) <= 50000:
            return fields, "Das Gewicht bitte als ganze Zahl in Gramm angeben (1 bis 50000)."
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
        if action == "delete" and copter_id:
            db.delete_copter(copter_id, email)
            return redirect(url_for("konto.copters", gespeichert="geloescht"))
        fields, error = _copter_fields(request.form)
        form_values = fields
        if error is None and action == "update" and copter_id:
            if not db.update_copter(copter_id, email, fields):
                error = "Dieser Copter wurde nicht gefunden."
            else:
                return redirect(url_for("konto.copters", gespeichert="aktualisiert") + f"#copter-{copter_id}")
        elif error is None and action == "create":
            if db.count_copters(email) >= MAX_COPTERS:
                error = f"Du kannst höchstens {MAX_COPTERS} Copter speichern."
            else:
                new_id = db.add_copter(email, fields)
                return redirect(url_for("konto.copters", gespeichert="angelegt") + f"#copter-{new_id}")
        edit_id = copter_id if action == "update" else None
    editing = db.get_copter(edit_id, email) if edit_id else None
    if editing is not None and not form_values:
        form_values = dict(editing)
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
    )
