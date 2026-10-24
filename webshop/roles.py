"""Rollen der Benutzerkonten (accounts.role) und was sie duerfen.

- admin      Administrator: volle Admin-Rechte wie der Admin-Login aus der .env
             (Dashboard, Benutzer, Rollen, Shop, Forum-Kategorien …).
- moderator  Moderator: Forum moderieren (Beitraege bearbeiten/loeschen, Themen
             verschieben, sperren, anheften, loeschen) und Leaderboard-Eintraege
             pruefen bzw. loeschen.
- support, tester, vip: nur Titel und Abzeichen im Forum und im Profil.

Ein eingeloggtes Konto mit Rolle "admin" bekommt bei jeder Anfrage
session["is_admin"] (sync_session); verliert es die Rolle, faellt das Recht
bei der naechsten Anfrage weg. Der Admin-Login aus der .env bleibt davon
unberuehrt.
"""

from flask import g, session

import db

# Reihenfolge = Anzeige in der Auswahl. title: Titel im Forum, badge: Abzeichen.
ROLES = {
    "admin": {"title": "Administrator", "badge": "Admin"},
    "moderator": {"title": "Moderator", "badge": "Mod"},
    "support": {"title": "Support", "badge": "Support"},
    "tester": {"title": "Tester", "badge": "Tester"},
    "vip": {"title": "VIP", "badge": "VIP"},
}
ROLE_LABELS = {
    "admin": "Administrator – volle Admin-Rechte",
    "moderator": "Moderator – Forum moderieren, Leaderboard prüfen",
    "support": "Support – nur Titel und Abzeichen",
    "tester": "Tester – nur Titel und Abzeichen",
    "vip": "VIP – nur Titel und Abzeichen",
}


def valid(role):
    return role in ROLES


def current_account():
    if "role_account" not in g:
        email = session.get("account_email")
        g.role_account = db.get_account_by_email(email) if email else None
    return g.role_account


def current_role():
    account = current_account()
    return account["role"] if account is not None and account["role"] in ROLES else None


def is_admin():
    return bool(session.get("is_admin"))


def is_moderator():
    return is_admin() or current_role() == "moderator"


def sync_session():
    """before_request: Admin-Rechte an die Konto-Rolle koppeln."""
    if not session.get("account_email") and not session.get("admin_via_role"):
        return
    if current_role() == "admin":
        if not session.get("is_admin"):
            session["is_admin"] = True
            session["admin_via_role"] = True
    elif session.pop("admin_via_role", None):
        session.pop("is_admin", None)


def register(app):
    app.before_request(sync_session)
    app.add_template_global(ROLES, "roles")
