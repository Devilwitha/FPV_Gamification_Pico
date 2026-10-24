"""Mehrsprachigkeit der Webseite: Deutsch (Quelltext), Englisch, Franzoesisch,
Italienisch und Spanisch.

- Texte stehen im Code und in den Templates auf Deutsch und werden mit _()
  uebersetzt: {{ _("Konto erstellen") }} bzw. _("Höchstens {n} Copter.", n=50).
  Fehlt eine Uebersetzung, erscheint der deutsche Text.
- Uebersetzungen: translations/<bereich>/<sprache>.json, je {"deutscher Text": "Uebersetzung"}.
  Bereiche: "site" (diese Flask-App), "tools" (Drohnen Tools) und "viewer"
  (Blackbox Viewer) - die beiden letzten holt static/js/i18n-dom.js ueber
  /i18n/<bereich>/<sprache>.json und uebersetzt die Seiten im Browser.
- Sprache: ?lang=xx, sonst das Cookie "lang" (gilt fuer die ganze Domain, auch
  Drohnen Tools und Blackbox Viewer), sonst die Browsersprache, sonst Deutsch.
  Gewaehlt wird sie im Menue oben (templates/_nav.html, /sprache/<code>).
"""

import json
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, abort, g, has_request_context, jsonify, redirect, request

LANGUAGES = {"de": "Deutsch", "en": "English", "fr": "Français", "it": "Italiano", "es": "Español"}
DEFAULT = "de"
COOKIE = "lang"
COOKIE_MAX_AGE = 365 * 24 * 3600
BUNDLES = ("site", "tools", "viewer")
CATALOG_DIR = Path(__file__).resolve().parent / "translations"

bp = Blueprint("i18n", __name__)
_catalogs = {}


def catalog(bundle, lang):
    """{deutscher Text: Uebersetzung} - einmal geladen und dann im Speicher."""
    key = (bundle, lang)
    if key not in _catalogs:
        path = CATALOG_DIR / bundle / f"{lang}.json"
        try:
            _catalogs[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _catalogs[key] = {}
    return _catalogs[key]


def _valid(code):
    code = (code or "").strip().lower()[:2]
    return code if code in LANGUAGES else None


def current_lang():
    """Sprache der laufenden Anfrage (ausserhalb einer Anfrage: Deutsch)."""
    if not has_request_context():
        return DEFAULT
    if "lang" not in g:
        g.lang = (
            _valid(request.args.get("lang"))
            or _valid(request.cookies.get(COOKIE))
            or request.accept_languages.best_match(LANGUAGES.keys())
            or DEFAULT
        )
    return g.lang


def gettext(text, **values):
    """Uebersetzt einen deutschen Text; Platzhalter {name} werden danach eingesetzt."""
    lang = current_lang()
    translated = text if lang == DEFAULT else catalog("site", lang).get(text)
    if translated is None:
        translated = text
    if values:
        try:
            return translated.format(**values)
        except (KeyError, IndexError, ValueError):
            return text.format(**values)
    return translated


def _safe_next(value):
    value = value or "/"
    return value if value.startswith("/") and not value.startswith("//") else "/"


@bp.get("/sprache/<code>")
def switch(code):
    """Sprache waehlen (Cookie fuer die ganze Domain) und zur Seite zurueck."""
    lang = _valid(code)
    target = request.args.get("next")
    if not target and request.referrer:
        parts = urlsplit(request.referrer)
        target = parts.path + (f"?{parts.query}" if parts.query else "")
    response = redirect(_safe_next(target))
    if lang:
        response.set_cookie(COOKIE, lang, max_age=COOKIE_MAX_AGE, path="/", samesite="Lax")
    return response


@bp.get("/i18n/<bundle>/<code>.json")
def bundle_catalog(bundle, code):
    """Uebersetzungen fuer Drohnen Tools und Blackbox Viewer (static/js/i18n-dom.js)."""
    lang = _valid(code)
    if bundle not in BUNDLES or lang is None:
        abort(404)
    response = jsonify(catalog(bundle, lang) if lang != DEFAULT else {})
    response.headers["Cache-Control"] = "public, max-age=60"
    return response


def _remember_choice(response):
    """?lang=xx merkt sich die Wahl wie das Menue."""
    lang = _valid(request.args.get("lang"))
    if lang and request.cookies.get(COOKIE) != lang:
        response.set_cookie(COOKIE, lang, max_age=COOKIE_MAX_AGE, path="/", samesite="Lax")
    return response


def register(app):
    app.register_blueprint(bp)
    app.after_request(_remember_choice)
    app.jinja_env.globals.update(_=gettext, current_lang=current_lang, languages=LANGUAGES)
