"""Webshop-Anwendung mit echter Stripe- und PayPal-Zahlungsanbindung."""

import io
import json
import os
import re
import secrets
import sys
import time
import zipfile
from datetime import datetime, timezone
from functools import wraps

import requests
import stripe
from dotenv import load_dotenv
from flask import Flask, Response, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db
import orders_db

load_dotenv()

# DUMMY_MODE (Default: aus) - siehe webshop/CLAUDE.md Abschnitt "Ausnahme
# (lokaler Dev-Schalter)": schaltet AUSSCHLIESSLICH lokal/zu Testzwecken einen
# zusaetzlichen "Dummy-Kauf simulieren"-Button frei, der Stripe/PayPal komplett
# umgeht. Ruehrt die echten Zahlungspfade nicht an und ist standardmaessig aus.
DUMMY_MODE = os.environ.get("DUMMY_MODE", "true").strip().lower() == "true"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "geheim_schluessel_change_me")

# Offline-Lizenzsystem (siehe tools/license_generator.py und
# source/license_verifier.py): der Webshop-Server signiert license.lic-Dateien
# selbst, da Endkunden (anders als beim seriellen GUI-Flow in
# tools/build_firmware.py) keine USB-Seriell-Verbindung zu diesem Rechner
# haben. PROJECT_ROOT ist das Elternverzeichnis von webshop/.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(PROJECT_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

try:
    import license_generator
except Exception:
    license_generator = None

KEYS_DIR = os.environ.get("LICENSE_KEYS_DIR", "").strip() or os.path.join(PROJECT_ROOT, "keys")
PRIVATE_KEY_PATH = os.environ.get("LICENSE_PRIVATE_KEY_PATH", "").strip() or os.path.join(KEYS_DIR, "private_key.pem")
PUBLIC_KEY_PATH = os.environ.get("LICENSE_PUBLIC_KEY_PATH", "").strip() or os.path.join(KEYS_DIR, "public_key.pem")
# Lokales Archiv aller ueber den Webshop ausgestellten Lizenzen (siehe
# _save_license_record()) - analog zu build_firmware.py's LICENSES_DIR, siehe
# .gitignore ("/lizenzen"), landet nie im Repo (enthaelt Kundendaten).
LICENSES_DIR = os.path.join(PROJECT_ROOT, "lizenzen")

# Persistente Ablage fuer offene Bestellungen und bereits ausgestellte
# Lizenzen (Login-/Konto-Bereich, siehe db.py) - ermoeglicht dem Kunden, sich
# spaeter per E-Mail-Adresse erneut einzuloggen, statt nur der Flask-Session
# zu vertrauen.
db.init_db()

# Separate, eigenstaendige Datenbank ausschliesslich fuer Hardware-Bestellungen
# (Versandadressen) - siehe orders_db.py's Modul-Docstring: bewusst getrennt
# von webshop.db und wird nie geloescht/ersetzt.
orders_db.init_db()

# Admin-Bereich (siehe /admin, /admin/login): Zugangsdaten kommen AUSSCHLIESSLICH
# aus der .env-Datei, niemals hartkodiert. Ohne gesetzte Zugangsdaten bleibt der
# Admin-Bereich serverseitig gesperrt (siehe _admin_login_configured()).
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

# Pico-Hardware-IDs sind hexadezimale machine.unique_id()-Strings (8 Bytes ->
# 16 Hex-Zeichen bei RP2040/RP2350) - Bereich bewusst etwas weiter gefasst
# (8-32 Zeichen), damit zukuenftige Board-Varianten mit abweichender ID-Laenge
# nicht hart ausgeschlossen werden.
HARDWARE_ID_PATTERN = re.compile(r"^[0-9a-f]{8,32}$")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET")
PAYPAL_MODE = os.environ.get("PAYPAL_MODE", "sandbox")
DOMAIN_URL = os.environ.get("DOMAIN_URL", "http://localhost:5000")

stripe.api_key = STRIPE_SECRET_KEY

PAYPAL_API_BASE_URL = (
    "https://api-m.paypal.com"
    if PAYPAL_MODE == "live"
    else "https://api-m.sandbox.paypal.com"
)

PRODUCTS = {
    "software-lizenz": {
        "id": "software-lizenz",
        "name": "Software-Lizenz",
        "description": (
            "Digitale Firmware-Lizenz für den FPV Gamification Pico. Enthält "
            "Trick-Erkennung, Live-Highscore-System, Real-Time Mini-Games, "
            "Infection-Modus (BLE), Shooter-Modus (Infrarot-Laser-Tag) und "
            "OTA-Updates. Neu: erweiterbares Plugin-System - installiere "
            "zusätzliche Mods direkt aus diesem Webshop-Store per Knopfdruck "
            "auf dem Pico, inklusive automatischer Absturzerkennung pro Mod. "
            "Dazu passend die kostenlose Android-App zur Verwaltung "
            "installierter Plugins, Durchsuchen des Webshop-Stores und "
            "Auslösen von Firmware-Updates direkt vom Smartphone aus. "
            "Sofortige digitale Freischaltung nach dem Kauf."
        ),
        "type": "digital",
        "price_cents": 495,
        "currency": "chf",
        "image": "bilder/shop/lizenz/Gemini_Generated_Image_f7pr0tf7pr0tf7pr.png",
    },
    "hardware-lizenz": {
        "id": "hardware-lizenz",
        "name": "Hardware + Lizenz",
        "description": (
            "Raspberry Pi Pico inklusive vorinstallierter FPV Gamification "
            "Firmware. Einfach per CRSF-UART am Flight Controller anschließen "
            "(GND, CRSF TX auf GP1, optional 5V) und direkt lossfliegen. "
            "Inklusive erweiterbarem Plugin-System (Mods direkt aus dem "
            "Webshop-Store installierbar) und kostenloser Android-App zur "
            "Plugin-/Update-Verwaltung."
        ),
        "type": "physical",
        "price_cents": 1995,
        "currency": "chf",
        "image": "bilder/shop/Hardware/Gemini_Generated_Image_8jui9u8jui9u8jui.png",
    },
    "gatehill-gratis": {
        "id": "gatehill-gratis",
        "name": "Gate/Hill Pico (kostenlos)",
        "description": (
            "Richte einen zusätzlichen Pico als stationären King-of-the-Hill-"
            "Hügel bzw. als Race-Tor ein - komplett kostenlos, ohne Anmeldung "
            "und ohne Lizenz nutzbar. Läuft mit derselben Firmware wie der "
            "Gamification Pico, nur mit der Geräte-Rolle „Gate/Hill“."
        ),
        "type": "free",
        "price_cents": 0,
        "currency": "chf",
        "image": "bilder/shop/Hardware/Gemini_Generated_Image_8jui9u8jui9u8jui.png",
    },
}


# ==================== Plugin-Store ====================
# Verteilt Mods (siehe source/plugin_manager.py) an den Pico und die Android-
# App: jedes Mod liegt als entpackter, FLACHER Ordner unter
# static/plugins_store/<name>/ (manifest.json + main.py, siehe
# template/README.md) und wird ueber Flasks eingebautes static-Serving
# direkt ausgeliefert - kein eigener Download-Endpunkt noetig. /api/plugins
# liefert dieselbe Liste inkl. Dateinamen als JSON (fuer
# source/network_manager.py's Boot-Sync sowie die Android-App).
PLUGINS_STORE_DIR = os.path.join(app.root_path, "static", "plugins_store")
PLUGIN_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
# Namen der mit der Firmware mitgelieferten Standard-Mods (siehe
# source/mods/) - ein oeffentlicher Kunden-Upload darf diese NIEMALS
# ueberschreiben/loeschen, nur ein Admin-Upload darf das (bewusste
# Ausnahme, siehe _process_plugin_zip_upload()'s allow_reserved_names).
RESERVED_PLUGIN_NAMES = frozenset({"shooter", "example_plugin"})
# MicroPython .mpy-Dateien beginnen immer mit dem Byte 'M' (0x4D), gefolgt
# von Versions-/Feature-Bytes (siehe MicroPythons persistentcode-Format).
# Das ist KEINE Ausfuehrungs-/Funktionspruefung (die ist auf dem Webshop-
# Server unmoeglich - .mpy-Bytecode ist MicroPython-spezifisch, CPython
# kann ihn nicht laden, und ein hochgeladenes Plugin serverseitig
# tatsaechlich AUSZUFUEHREN waere ein Codeausfuehrungs-Sicherheitsrisiko,
# das dieser Webshop bewusst nicht eingeht) - nur eine strukturelle
# Plausibilitaetspruefung gegen offensichtlich falsche/leere Dateien.
MPY_MAGIC_BYTE = 0x4D
# Rohe Quellcode-Vorlage des mitgelieferten shooter-Plugins (.py, NICHT
# .mpy-kompiliert) - dient als Lern-/Template-Download fuer eigene
# Spielmodus-Plugins (siehe template/README.md), separat vom normalen
# Plugin-Store (der ausschliesslich kompiliertes .mpy akzeptiert).
SHOOTER_TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "source", "mods", "shooter")
SHOOTER_TEMPLATE_FILES = ("manifest.json", "main.py", "ir_emitter.py", "ir_receiver.py", "admin_shooter.html")
# Leeres Boilerplate fuer ein komplett neues, eigenes Plugin (siehe
# template/README.md) - anders als SHOOTER_TEMPLATE_* (voll funktionsfaehiges
# Beispiel-Plugin) enthaelt dieser Ordner nur auskommentierte Hooks/Slots
# ohne echte Spiellogik, gedacht als Startpunkt zum Kopieren nach
# source/mods/<eigener_name>/.
PLUGIN_TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "template", "plugin_template")
PLUGIN_TEMPLATE_FILES = ("manifest.json", "main.py")


def _list_store_plugins():
    """Liest alle im Webshop hochgeladenen Mods aus static/plugins_store/
    (jeder Unterordner mit einer manifest.json gilt als ein Mod)."""
    plugins = []
    try:
        entries = sorted(os.listdir(PLUGINS_STORE_DIR))
    except Exception:
        entries = []
    for name in entries:
        plugin_dir = os.path.join(PLUGINS_STORE_DIR, name)
        manifest_path = os.path.join(plugin_dir, "manifest.json")
        if not os.path.isdir(plugin_dir) or not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
        except Exception:
            continue
        files = sorted(
            filename
            for filename in os.listdir(plugin_dir)
            if os.path.isfile(os.path.join(plugin_dir, filename))
        )
        uploaded_by = manifest.get("uploaded_by", "")
        uploaded_by_account = db.get_account_by_email(uploaded_by) if uploaded_by else None
        plugins.append(
            {
                "name": manifest.get("name", name),
                "version": manifest.get("version", "0.0.0"),
                "author": manifest.get("author", ""),
                "description": manifest.get("description", ""),
                "uploaded_by": uploaded_by,
                # Zeigt den registrierten Namen statt der rohen E-Mail-Adresse,
                # falls ein Konto existiert (siehe db.py's accounts-Tabelle) -
                # faellt sonst auf die E-Mail-Adresse selbst zurueck (z.B. "admin").
                "uploaded_by_display": uploaded_by_account["full_name"] if uploaded_by_account else uploaded_by,
                "files": files,
            }
        )
    return plugins


def _is_safe_plugin_zip_member(member_name):
    """Zip-Slip-Schutz: lehnt absolute Pfade und '..'-Segmente ab, bevor
    ueberhaupt in die ZIP-Datei hineingelesen wird."""
    normalized = member_name.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        return False
    parts = [part for part in normalized.split("/") if part]
    return ".." not in parts


def format_price(price_cents):
    """Formatiert einen Rappen-Betrag als Schweizer-Franken-Zeichenkette
    (Schweizer Zahlenformat: Punkt als Dezimaltrennzeichen, Apostroph als
    Tausendertrennzeichen, z. B. 1995 -> "19.95 CHF")."""
    franken_betrag = price_cents / 100
    formatiert = f"{franken_betrag:,.2f}".replace(",", "'")
    return f"{formatiert} CHF"


app.jinja_env.filters["format_price"] = format_price


@app.context_processor
def inject_dummy_mode():
    """Macht dummy_mode in allen Templates verfuegbar (siehe base.html-Banner
    und den bedingt eingeblendeten Dummy-Kauf-Button in checkout.html)."""
    return {"dummy_mode": DUMMY_MODE}


def get_paypal_access_token():
    """Holt ein OAuth2-Access-Token von der PayPal-API (Client-Credentials-Flow)."""
    response = requests.post(
        f"{PAYPAL_API_BASE_URL}/v1/oauth2/token",
        headers={"Accept": "application/json", "Accept-Language": "de_DE"},
        data={"grant_type": "client_credentials"},
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["access_token"]


@app.route("/")
def index():
    """Startseite mit Produktvorstellung."""
    return render_template("index.html")


@app.route("/shop")
def shop():
    """Shop-Übersicht mit allen verfügbaren Artikeln."""
    return render_template("shop.html", products=PRODUCTS.values())


@app.route("/plugins")
def plugins_store():
    """Oeffentliche Plugin-Store-Seite - listet alle im Admin-Bereich
    hochgeladenen Mods (siehe /admin/plugins/upload)."""
    return render_template("plugins.html", plugins=_list_store_plugins())


@app.route("/plugins/shooter-template.zip")
def plugins_shooter_template():
    """Stellt den ROHEN Quellcode des mitgelieferten shooter-Plugins (.py,
    NICHT .mpy-kompiliert) als ZIP-Download bereit - eine Lern-Vorlage
    dafuer, wie ein eigener Spielmodus komplett als Plugin gebaut wird
    (Spiellogik + eigene Hardware-Treiber + eigene Weboberflaeche, siehe
    source/mods/shooter/main.py's Docstring). Kein direkt installierbares
    Store-Produkt - dafuer muss der Code zuerst per mpy-cross kompiliert
    werden (siehe tools/plugin_packager.py), genau wie jeder andere
    Kunden-Upload auch (_process_plugin_zip_upload() lehnt rohe .py-Dateien
    im normalen Store konsequent ab)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in SHOOTER_TEMPLATE_FILES:
            file_path = os.path.join(SHOOTER_TEMPLATE_DIR, filename)
            if os.path.isfile(file_path):
                archive.write(file_path, arcname=filename)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="shooter_plugin_template.zip",
    )


@app.route("/plugins/plugin-template.zip")
def plugins_plugin_template():
    """Stellt das leere Plugin-Boilerplate aus template/plugin_template/ als
    ZIP-Download bereit - Startpunkt fuer ein komplett eigenes Plugin (siehe
    template/README.md), im Gegensatz zu plugins_shooter_template() oben
    (vollstaendiges, funktionierendes Beispiel-Plugin). Liest die Dateien bei
    jedem Request frisch von der Platte, bleibt also automatisch aktuell,
    sobald sich template/plugin_template/ aendert - kein separater Build-
    Schritt noetig."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in PLUGIN_TEMPLATE_FILES:
            file_path = os.path.join(PLUGIN_TEMPLATE_DIR, filename)
            if os.path.isfile(file_path):
                archive.write(file_path, arcname=filename)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name="plugin_template.zip",
    )


@app.route("/plugins/<name>/download")
def plugins_download(name):
    """Packt alle Dateien eines im Store hochgeladenen Mods (siehe
    _list_store_plugins()) zu einer ZIP-Datei und bietet sie zum Download
    an - fuer menschliche Besucher der /plugins-Seite im Browser (Pico und
    Android-App laden dieselben Dateien stattdessen einzeln direkt ueber
    Flasks static-Serving, siehe network_manager.download_plugin_via_wifi()).
    404, falls der Mod nicht existiert (kein Ausnahmefehler bei unbekannter
    ID, siehe webshop/CLAUDE.md Abschnitt 6)."""
    plugin_dir = os.path.join(PLUGINS_STORE_DIR, name)
    manifest_path = os.path.join(plugin_dir, "manifest.json")
    if not os.path.isfile(manifest_path):
        return render_template("cancel.html", message="Dieses Plugin existiert nicht."), 404

    files = sorted(
        filename
        for filename in os.listdir(plugin_dir)
        if os.path.isfile(os.path.join(plugin_dir, filename))
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename in files:
            archive.write(os.path.join(plugin_dir, filename), arcname=filename)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{name}.zip",
    )


@app.route("/api/plugins")
def api_plugins():
    """JSON-Mod-Liste fuer den Pico (source/network_manager.py's Boot-Sync
    und WLAN-Download) sowie fuer die Android-App."""
    return jsonify({"plugins": _list_store_plugins()})


@app.route("/gatehill-install")
def gatehill_install():
    """Anleitungsseite für die kostenlose Einrichtung eines "Gate/Hill Pico"
    (stationärer King-of-the-Hill-Hügel/Race-Tor, siehe source/role_setup.py
    und source/main_gatehill.py) - öffentlich erreichbar ohne Kauf und ohne
    Anmeldung (kein Bezug zu PRODUCTS/Checkout-Session nötig). Die Rolle
    "Gate/Hill" sperrt laut Spezifikation (siehe main_gatehill.py) keinerlei
    Funktionen ohne Lizenz, daher zeigt diese Seite denselben Installer-
    Workflow wie nach einem Lizenzkauf (siehe /license-setup bzw.
    _render_license_setup()), aber ohne den Lizenz-Schritt und ohne die
    Hardware-ID-Anleitung - dafür mit dem zusätzlichen Schritt zur Auswahl
    der Geräte-Rolle "Gate/Hill Pico" bei der Ersteinrichtung."""
    return render_template("gatehill_install.html")


@app.route("/checkout/<product_id>")
def checkout(product_id):
    """Checkout-Seite für ein bestimmtes Produkt mit Auswahl der Zahlungsmethode.

    Ist der Kunde bereits "eingeloggt" (siehe _current_account_email()), wird
    dessen E-Mail-Adresse vorausgefüllt und im Frontend gesperrt - der Kauf
    läuft dann automatisch über diese Adresse, ohne dass sie erneut
    eingetippt werden muss."""
    product = PRODUCTS.get(product_id)
    if product is None:
        return render_template("cancel.html", message="Dieses Produkt existiert nicht."), 404
    return render_template(
        "checkout.html",
        product=product,
        stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
        paypal_client_id=PAYPAL_CLIENT_ID,
        account_email=_current_account_email(),
    )


@app.route("/api/stripe/create-checkout-session", methods=["POST"])
def stripe_create_checkout_session():
    """Erstellt eine echte Stripe Checkout Session und liefert die Weiterleitungs-URL."""
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    product = PRODUCTS.get(product_id)
    email = (data.get("email") or "").strip()

    if product is None:
        return jsonify({"error": "Unbekanntes Produkt."}), 404
    if "@" not in email:
        return jsonify({"error": "Bitte eine gültige E-Mail-Adresse angeben."}), 400

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=email,
            line_items=[
                {
                    "price_data": {
                        "currency": product["currency"],
                        "product_data": {
                            "name": product["name"],
                            "description": product["description"],
                        },
                        "unit_amount": product["price_cents"],
                    },
                    "quantity": 1,
                }
            ],
            success_url=f"{DOMAIN_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN_URL}/cancel",
            metadata={"product_id": product["id"], "email": email},
        )
    except stripe.error.StripeError as fehler:
        return jsonify({"error": str(fehler)}), 400

    # Wird serverseitig in /success bzw. /license-setup ausgelesen - siehe dort:
    # der Kunde hat beim (echten) Stripe-Checkout keinen direkten Rueckkanal
    # ausser diesem Redirect, daher merken wir uns Produkt/E-Mail bereits jetzt
    # in der signierten Flask-Session statt sie ungeschuetzt per Query-Parameter
    # durch die Weiterleitung zu reichen.
    session["checkout_product_id"] = product["id"]
    session["checkout_email"] = email

    return jsonify({"checkout_url": checkout_session.url})


@app.route("/api/paypal/create-order", methods=["POST"])
def paypal_create_order():
    """Erstellt eine echte PayPal-Bestellung über die Orders v2 API."""
    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    product = PRODUCTS.get(product_id)
    email = (data.get("email") or "").strip()

    if product is None:
        return jsonify({"error": "Unbekanntes Produkt."}), 404
    if "@" not in email:
        return jsonify({"error": "Bitte eine gültige E-Mail-Adresse angeben."}), 400

    try:
        access_token = get_paypal_access_token()
    except requests.RequestException as fehler:
        return jsonify({"error": f"PayPal-Authentifizierung fehlgeschlagen: {fehler}"}), 502

    # Siehe Kommentar im Stripe-Pendant oben: Produkt/E-Mail werden bereits vor
    # der Weiterleitung zu PayPal in der Flask-Session gemerkt.
    session["checkout_product_id"] = product["id"]
    session["checkout_email"] = email

    betrag = f"{product['price_cents'] / 100:.2f}"

    order_payload = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": product["id"],
                "description": product["name"],
                "custom_id": email,
                "amount": {
                    "currency_code": product["currency"].upper(),
                    "value": betrag,
                },
            }
        ],
        "application_context": {
            "return_url": f"{DOMAIN_URL}/success",
            "cancel_url": f"{DOMAIN_URL}/cancel",
            "user_action": "PAY_NOW",
        },
    }

    response = requests.post(
        f"{PAYPAL_API_BASE_URL}/v2/checkout/orders",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        json=order_payload,
        timeout=15,
    )

    if response.status_code not in (200, 201):
        return jsonify({"error": "PayPal-Bestellung konnte nicht erstellt werden.", "details": response.text}), 502

    order = response.json()
    return jsonify({"id": order["id"]})


@app.route("/api/paypal/capture-order/<order_id>", methods=["POST"])
def paypal_capture_order(order_id):
    """Erfasst (captured) eine bestätigte PayPal-Bestellung über die Orders v2 API."""
    try:
        access_token = get_paypal_access_token()
    except requests.RequestException as fehler:
        return jsonify({"error": f"PayPal-Authentifizierung fehlgeschlagen: {fehler}"}), 502

    response = requests.post(
        f"{PAYPAL_API_BASE_URL}/v2/checkout/orders/{order_id}/capture",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=15,
    )

    if response.status_code not in (200, 201):
        return jsonify({"error": "PayPal-Zahlung konnte nicht erfasst werden.", "details": response.text}), 502

    capture_data = response.json()

    # Zahlung ist erst jetzt (nach der echten Capture-Antwort von PayPal) als
    # gueltig zu betrachten - siehe _record_valid_purchase(). Vorher wurde in
    # paypal_create_order() nur die Bestellabsicht in der Session gemerkt.
    if capture_data.get("status") == "COMPLETED":
        product = PRODUCTS.get(session.get("checkout_product_id"))
        email = session.get("checkout_email")
        if product is not None and email:
            _record_valid_purchase(product, email, "paypal", order_id)

    return jsonify(capture_data)


@app.route("/api/dummy/create-purchase", methods=["POST"])
def dummy_create_purchase():
    """Simuliert einen abgeschlossenen Kauf OHNE Stripe/PayPal anzusprechen.

    Nur erreichbar, wenn DUMMY_MODE aktiv ist (siehe CLAUDE.md, Abschnitt
    "Ausnahme (lokaler Dev-Schalter)") - dient ausschließlich dazu, den
    Lizenz-Einrichtungs-Flow (/license-setup) lokal ohne echte Zahlungsdaten
    durchklicken zu können."""
    if not DUMMY_MODE:
        return jsonify({"error": "Dummy-Modus ist nicht aktiv."}), 403

    data = request.get_json(silent=True) or {}
    product_id = data.get("product_id")
    product = PRODUCTS.get(product_id)
    if product is None:
        return jsonify({"error": "Unbekanntes Produkt."}), 404

    email = (data.get("email") or "").strip() or "dummy@example.test"
    if "@" not in email:
        return jsonify({"error": "Bitte eine gültige E-Mail-Adresse angeben."}), 400

    session["checkout_product_id"] = product["id"]
    session["checkout_email"] = email

    # DUMMY_MODE ist ausschließlich für lokale Entwicklung/Tests gedacht (siehe
    # CLAUDE.md) - die "Zahlung" gilt hier per Definition sofort als gültig.
    order_id = f"DUMMY-{int(time.time())}-{secrets.token_hex(4)}"
    _record_valid_purchase(product, email, "dummy", order_id)

    return jsonify({"redirect_url": url_for("success", order_id=order_id)})


def _record_valid_purchase(product, email, payment_provider, payment_reference):
    """Wird aufgerufen, sobald eine Zahlung als gültig bestätigt wurde (echte
    Stripe-/PayPal-Prüfung bzw. DUMMY_MODE).

    - Digitale Produkte (Software-Lizenz): legt einen offenen Eintrag in der
      pending_licenses-Tabelle an (siehe db.py) und merkt dessen ID in der
      Session (checkout_pending_id), damit der Direkt-Flow (ohne Login)
      unmittelbar zu /license-setup weiterleiten kann.
    - Physische Produkte (Hardware): legt analog einen offenen Eintrag in der
      pending_shipping-Tabelle an (siehe orders_db.py, komplett getrennte
      Datenbank) und merkt dessen ID in checkout_pending_shipping_id, damit
      /account den Hinweis auf die noch fehlende Versandadresse anzeigen kann
      (siehe /shipping-setup) - exakt dasselbe Muster wie bei der Lizenz.

    Idempotent gegenüber mehrfachen Aufrufen mit derselben Zahlungsreferenz
    (z. B. Reload von /success)."""
    if product["type"] == "digital":
        if db.payment_already_recorded(payment_provider, payment_reference):
            return
        pending_id = db.add_pending_license(email, product["id"], payment_provider, payment_reference)
        session["checkout_pending_id"] = pending_id
        session["checkout_product_id"] = product["id"]
        session["checkout_email"] = email
    elif product["type"] == "physical":
        if orders_db.payment_already_recorded(payment_provider, payment_reference):
            return
        pending_shipping_id = orders_db.add_pending_shipping(email, product["id"], payment_provider, payment_reference)
        session["checkout_pending_shipping_id"] = pending_shipping_id
        session["checkout_product_id"] = product["id"]
        session["checkout_email"] = email


@app.route("/success")
def success():
    """Erfolgsseite nach abgeschlossener Zahlung.

    Digitale Produkte (Software-Lizenz) UND physische Produkte (Hardware)
    werden direkt zu "Mein Konto" weitergeleitet (nicht mehr direkt zur
    Hardware-ID-Eingabe bzw. zum Adressformular) - dort steht oben ein
    hervorgehobener Button zur jeweils offenen Einrichtung (Lizenz bzw.
    Versandadresse), siehe account_licenses.html. Grundlage für die
    Weiterleitung ist der jeweils offene pending_licenses-/pending_shipping-
    Eintrag (siehe _record_valid_purchase()), nicht der (client-kontrollierbare)
    Query-String. Bei Rückkehr von Stripe wird die Zahlung hier zusätzlich
    serverseitig gegen die Stripe-API verifiziert, da Stripe (anders als
    PayPal) keinen serverseitigen Capture-Schritt vor dieser Weiterleitung
    kennt."""
    session_id = request.args.get("session_id")
    order_id = request.args.get("order_id")

    if session_id and not db.payment_already_recorded("stripe", session_id):
        try:
            stripe_session = stripe.checkout.Session.retrieve(session_id)
        except stripe.error.StripeError:
            stripe_session = None

        if stripe_session is not None and stripe_session.payment_status == "paid":
            product = PRODUCTS.get(stripe_session.metadata.get("product_id"))
            email = stripe_session.metadata.get("email")
            if product is not None and email:
                _record_valid_purchase(product, email, "stripe", session_id)

    product = PRODUCTS.get(session.get("checkout_product_id"))
    if product is not None and product["type"] == "digital" and session.get("checkout_pending_id"):
        return redirect(url_for("account"))
    if product is not None and product["type"] == "physical" and session.get("checkout_pending_shipping_id"):
        return redirect(url_for("account"))

    return render_template("success.html", session_id=session_id, order_id=order_id)


@app.route("/cancel")
def cancel():
    """Abbruchseite bei nicht abgeschlossener Zahlung."""
    return render_template("cancel.html", message="Der Bezahlvorgang wurde abgebrochen.")


def _current_account_email():
    """Liefert die aktuell "eingeloggte" E-Mail-Adresse - entweder über den
    echten Login (/login, session["account_email"]) oder implizit direkt
    nach einem Kauf über die Checkout-Session (session["checkout_email"]).
    So landet man nach dem Kauf ohne einen expliziten Login-Schritt bereits
    auf der eigenen Konto-Seite."""
    return session.get("account_email") or session.get("checkout_email")


def _is_authorized_for_pending(pending):
    """Prüft, ob die aktuelle Session Zugriff auf die offene Bestellung
    ``pending`` haben darf - entweder weil sie im Direkt-Flow selbst dafür
    gesorgt hat (session["checkout_pending_id"]) oder weil die aktuell
    "eingeloggte" E-Mail-Adresse zur Bestellung passt."""
    if session.get("checkout_pending_id") == pending["id"]:
        return True
    email = (_current_account_email() or "").strip().lower()
    return bool(email) and email == pending["email"].strip().lower()


def _is_authorized_for_pending_shipping(pending):
    """Prüft, ob die aktuelle Session Zugriff auf die offene Hardware-
    Bestellung ``pending`` haben darf - analog zu _is_authorized_for_pending(),
    nur für orders_db.pending_shipping statt db.pending_licenses."""
    if session.get("checkout_pending_shipping_id") == pending["id"]:
        return True
    email = (_current_account_email() or "").strip().lower()
    return bool(email) and email == pending["email"].strip().lower()


def _render_license_setup(pending):
    """Rendert license_setup.html fuer eine offene Bestellung (sqlite3.Row
    aus pending_licenses) - gemeinsam genutzt vom Direkt-Flow (/license-setup)
    und vom Login-Flow (/account)."""
    product = PRODUCTS.get(pending["product_id"])
    return render_template(
        "license_setup.html",
        product=product,
        email=pending["email"],
        pending_id=pending["id"],
        logged_in=bool(_current_account_email()),
    )


@app.route("/license-setup")
def license_setup():
    """Anleitungsseite nach Kauf einer Software-Lizenz: MicroPython-Installation,
    Thonny-Skript zum Auslesen der Hardware-ID, Eingabefeld zur Lizenzerzeugung.
    Erreichbar über den offenen pending_licenses-Eintrag der aktuellen
    Checkout-Session ODER (über den Button auf /account) per expliziter
    ``pending_id`` in der Query-Zeichenkette."""
    pending_id = request.args.get("pending_id", type=int) or session.get("checkout_pending_id")
    pending = db.get_pending_license(pending_id)
    if pending is None or not _is_authorized_for_pending(pending):
        return render_template(
            "cancel.html",
            message="Keine gültige Lizenz-Bestellung gefunden. Bitte zuerst im Shop kaufen.",
        ), 403
    return _render_license_setup(pending)


REQUIRED_SHIPPING_FIELDS = ("full_name", "street_address", "postal_code", "city", "country")


@app.route("/shipping-setup", methods=["GET", "POST"])
def shipping_setup():
    """Formularseite nach Kauf der Hardware: fragt die Versandadresse ab,
    damit das Paket verschickt werden kann. Erreichbar über den offenen
    pending_shipping-Eintrag der aktuellen Checkout-Session ODER (über den
    Button auf /account) per expliziter ``pending_id`` in der Query-
    Zeichenkette - exakt dasselbe Muster wie /license-setup, nur für
    orders_db.pending_shipping statt db.pending_licenses."""
    pending_id = request.args.get("pending_id", type=int) or session.get("checkout_pending_shipping_id")
    pending = orders_db.get_pending_shipping(pending_id)
    if pending is None or not _is_authorized_for_pending_shipping(pending):
        return render_template(
            "cancel.html",
            message="Keine gültige Hardware-Bestellung gefunden. Bitte zuerst im Shop kaufen.",
        ), 403

    product = PRODUCTS.get(pending["product_id"])
    error = None

    if request.method == "POST":
        address = {field: (request.form.get(field) or "").strip() for field in REQUIRED_SHIPPING_FIELDS}
        address["address_line2"] = (request.form.get("address_line2") or "").strip()
        address["phone"] = (request.form.get("phone") or "").strip()

        missing = [field for field in REQUIRED_SHIPPING_FIELDS if not address[field]]
        if missing:
            error = "Bitte alle Pflichtfelder ausfüllen: Name, Straße, PLZ, Stadt und Land."
        else:
            ist_direkt_flow = session.get("checkout_pending_shipping_id") == pending["id"]
            orders_db.move_pending_to_order(pending["id"], address)

            if ist_direkt_flow:
                # Siehe api_license_create(): verhindert, dass mit derselben
                # Bestellung mehrfach unbemerkt weitere Adressen angelegt werden.
                session.pop("checkout_pending_shipping_id", None)
                session.pop("checkout_product_id", None)
                session.pop("checkout_email", None)
                session["account_email"] = pending["email"]

            return redirect(url_for("account", shipping_saved=1))

    return render_template(
        "shipping_setup.html",
        product=product,
        email=pending["email"],
        pending_id=pending["id"],
        logged_in=bool(_current_account_email()),
        error=error,
        form=request.form if request.method == "POST" else {},
    )


@app.route("/api/license/create", methods=["POST"])
def api_license_create():
    """Signiert eine license.lic für die vom Kunden per Thonny ausgelesene
    Hardware-ID und liefert sie zum Download. Der Kunde hat keine serielle
    Verbindung zu diesem Server (anders als beim GUI-Flow in
    tools/build_firmware.py) - daher wird die Hardware-ID manuell abgefragt
    statt sie automatisiert vom Gerät zu lesen.

    Erreichbar sowohl direkt nach dem Kauf (Session-basiert) als auch über den
    Login-Bereich /account - in beiden Fällen muss die angegebene pending_id
    zur aktuellen Session gehören (siehe _is_authorized_for_pending())."""
    data = request.get_json(silent=True) or {}
    pending_id = data.get("pending_id")
    pending = db.get_pending_license(pending_id)
    if pending is None:
        return jsonify({"error": "Keine gültige Lizenz-Bestellung gefunden. Bitte zuerst im Shop kaufen."}), 403

    if not _is_authorized_for_pending(pending):
        return jsonify({"error": "Keine Berechtigung für diese Bestellung."}), 403

    ist_direkt_flow = session.get("checkout_pending_id") == pending["id"]

    product = PRODUCTS.get(pending["product_id"])
    if product is None:
        return jsonify({"error": "Unbekanntes Produkt."}), 404

    if license_generator is None:
        return jsonify({"error": "Lizenzsystem serverseitig nicht verfügbar (Paket 'cryptography' fehlt)."}), 500

    if not (os.path.isfile(PRIVATE_KEY_PATH) and os.path.isfile(PUBLIC_KEY_PATH)):
        return jsonify({"error": "RSA-Schlüsselpaar für das Lizenzsystem wurde serverseitig noch nicht erzeugt."}), 500

    hardware_id = (data.get("hardware_id") or "").strip().lower()
    if not HARDWARE_ID_PATTERN.match(hardware_id):
        return jsonify(
            {"error": "Ungültige Hardware-ID. Bitte die Ausgabe des Thonny-Skripts unverändert einfügen."}
        ), 400

    try:
        license_content = license_generator.sign_license_from_key_file(
            PRIVATE_KEY_PATH, hardware_id, pending["email"],
        )
    except Exception as fehler:
        return jsonify({"error": f"Lizenz konnte nicht erzeugt werden: {fehler}"}), 500

    lic_path, _json_path = _save_license_record(hardware_id, pending["email"], license_content)
    license_filename = os.path.splitext(os.path.basename(lic_path))[0]

    # "Verschiebt" die E-Mail-Adresse von den offenen Bestellungen in die
    # ausgestellten Lizenzen (siehe db.py, move_pending_to_customer()).
    db.move_pending_to_customer(pending["id"], hardware_id, license_filename)

    if ist_direkt_flow:
        # checkout_pending_id/-product_id verwerfen: verhindert, dass mit
        # derselben Bestellung mehrfach unbemerkt weitere Lizenzen erzeugt
        # werden. account_email bleibt (bzw. wird jetzt gesetzt), damit
        # /account nach der Einrichtung weiterhin erreichbar ist und die
        # gerade erzeugte Lizenz in der Liste zeigt.
        session.pop("checkout_pending_id", None)
        session.pop("checkout_product_id", None)
        session.pop("checkout_email", None)
        session["account_email"] = pending["email"]

    response = Response(license_content, mimetype="application/octet-stream")
    response.headers["Content-Disposition"] = "attachment; filename=license.lic"
    return response


@app.route("/license-setup/public-key.pem")
def license_setup_public_key():
    """Liefert den öffentlichen Schlüssel zum Download - muss zusammen mit
    license.lic auf den Pico kopiert werden (siehe source/license_verifier.py)."""
    if not os.path.isfile(PUBLIC_KEY_PATH):
        return jsonify({"error": "Public Key nicht gefunden."}), 404
    return send_file(PUBLIC_KEY_PATH, mimetype="application/x-pem-file", as_attachment=True, download_name="public_key.pem")


REQUIRED_ACCOUNT_FIELDS = ("full_name", "address", "phone", "country")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login-Seite für den Konto-Bereich: E-Mail-Adresse + Passwort gegen das
    registrierte Konto (siehe db.py's accounts-Tabelle/register()) geprüft.
    Wer noch kein Konto hat, muss sich zuerst über /register registrieren -
    unabhängig davon, ob unter der E-Mail-Adresse bereits etwas gekauft
    wurde (Bestellungen und Login-Konto sind bewusst getrennte Konzepte)."""
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        account = db.get_account_by_email(email) if "@" in email else None
        if account is None or not check_password_hash(account["password_hash"], password):
            error = "E-Mail-Adresse oder Passwort falsch."
        else:
            session["account_email"] = account["email"]
            return redirect(url_for("account"))
    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    """Registrierung eines neuen Login-Kontos: E-Mail + Passwort + Name/
    Adresse/Telefon/Land (siehe REQUIRED_ACCOUNT_FIELDS) - unabhängig davon,
    ob unter der E-Mail-Adresse bereits eine Bestellung existiert. Loggt
    nach erfolgreicher Registrierung direkt ein (kein separater Bestätigungs-
    schritt), analog zum bisherigen Direkt-Login nach Kauf."""
    error = None
    form_values = {}
    if request.method == "POST":
        form_values = {key: (request.form.get(key) or "").strip() for key in ("email", "full_name", "address", "phone", "country")}
        email = form_values["email"]
        password = request.form.get("password") or ""

        if "@" not in email:
            error = "Bitte eine gültige E-Mail-Adresse eingeben."
        elif len(password) < 8:
            error = "Das Passwort muss mindestens 8 Zeichen lang sein."
        elif any(not form_values[field] for field in REQUIRED_ACCOUNT_FIELDS):
            error = "Bitte Name, Adresse, Telefon und Land vollständig ausfüllen."
        elif db.get_account_by_email(email) is not None:
            error = "Für diese E-Mail-Adresse existiert bereits ein Konto."
        else:
            db.create_account(
                email=email,
                password_hash=generate_password_hash(password),
                full_name=form_values["full_name"],
                address=form_values["address"],
                phone=form_values["phone"],
                country=form_values["country"],
            )
            session["account_email"] = email
            return redirect(url_for("account"))
    return render_template("register.html", error=error, values=form_values)


@app.route("/logout")
def logout():
    """Beendet die eingeloggte Konto-Sitzung - inklusive der impliziten
    "Login" über eine frische Checkout-Session (siehe _current_account_email()),
    damit /account danach tatsächlich nicht mehr erreichbar ist."""
    session.pop("account_email", None)
    session.pop("checkout_email", None)
    session.pop("checkout_product_id", None)
    session.pop("checkout_pending_id", None)
    session.pop("checkout_pending_shipping_id", None)
    return redirect(url_for("index"))


@app.route("/account")
def account():
    """Konto-Übersicht: zeigt oben hervorgehobene Buttons zur Lizenz-
    Einrichtung bzw. zur Versandadress-Eingabe, falls jeweils eine offene
    Bestellung existiert (dieselbe Zielseite wie direkt nach dem Kauf), und
    darunter die Liste der bereits ausgestellten Lizenzen sowie der
    aufgegebenen Hardware-Bestellungen."""
    email = _current_account_email()
    if not email:
        return redirect(url_for("login"))

    pending_rows = db.get_pending_licenses_for_email(email)
    licenses = db.get_customer_licenses_for_email(email)
    pending_shipping_rows = orders_db.get_pending_shipping_for_email(email)
    shipping_orders = orders_db.get_shipping_orders_for_email(email)
    return render_template(
        "account_licenses.html",
        email=email,
        account=db.get_account_by_email(email),
        licenses=licenses,
        products=PRODUCTS,
        pending=pending_rows[0] if pending_rows else None,
        pending_shipping=pending_shipping_rows[0] if pending_shipping_rows else None,
        shipping_orders=shipping_orders,
        shipping_saved=request.args.get("shipping_saved") == "1",
    )


@app.route("/account/download/<int:license_id>")
def account_download_license(license_id):
    """Liefert die license.lic-Datei einer bereits ausgestellten Lizenz zum
    erneuten Download - nur für den eingeloggten Besitzer der E-Mail-Adresse."""
    email = _current_account_email()
    if not email:
        return redirect(url_for("login"))

    record = db.get_customer_license(license_id)
    if record is None or record["email"].strip().lower() != email.strip().lower():
        return jsonify({"error": "Lizenz nicht gefunden."}), 404

    lic_path = os.path.join(LICENSES_DIR, record["license_filename"] + ".lic")
    if not os.path.isfile(lic_path):
        return jsonify({"error": "Lizenzdatei ist serverseitig nicht mehr vorhanden."}), 404

    return send_file(lic_path, mimetype="application/octet-stream", as_attachment=True, download_name="license.lic")


def _save_license_record(hardware_id, email, license_content):
    """Legt jede über den Webshop ausgestellte Lizenz dauerhaft unter
    LICENSES_DIR ab (license.lic + Begleit-JSON mit Hardware-ID/E-Mail/
    Zeitpunkt) - analog zu build_firmware.py's save_license_record(), damit
    beide Ausgabewege (seriell per GUI, digital per Webshop) im selben Ordner
    nachvollziehbar bleiben."""
    os.makedirs(LICENSES_DIR, exist_ok=True)

    issued_date = datetime.now().strftime("%Y%m%d")
    safe_hardware_id = "".join(c for c in hardware_id if c.isalnum()) or "unknown"

    # Nur noch Datum statt vollem Zeitstempel: eine erneute Lizenzausstellung
    # fuer dieselbe Hardware-ID am selben Tag ueberschreibt bewusst die
    # vorherige Datei (jede Hardware-ID soll genau eine aktuelle Lizenz haben).
    base_name = f"{safe_hardware_id}_{issued_date}"

    lic_path = os.path.join(LICENSES_DIR, base_name + ".lic")
    with open(lic_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(license_content)

    record = {
        "hardware_id": hardware_id,
        "customer_id": email,
        "issued_at": issued_date,
        "issued_via": "webshop-dummy" if DUMMY_MODE else "webshop",
    }
    json_path = os.path.join(LICENSES_DIR, base_name + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return lic_path, json_path


# ==================== Admin-Bereich ====================
# Komplett getrennt vom Kunden-Login (/login, /account): Zugangsdaten kommen
# ausschliesslich aus ADMIN_USERNAME/ADMIN_PASSWORD in der .env-Datei (siehe
# oben, niemals hartkodiert). Erreichbar ueber einen bewusst unauffaelligen
# Link unten rechts auf jeder Seite (siehe base.html, .admin-corner-link).

def _admin_login_configured():
    return bool(ADMIN_USERNAME) and bool(ADMIN_PASSWORD)


def admin_required(view_func):
    """Decorator: leitet zu /admin/login um, falls die aktuelle Session nicht
    als Admin authentifiziert ist (session["is_admin"])."""

    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return view_func(*args, **kwargs)

    return wrapped_view


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Login-Seite für den Admin-Bereich - komplett getrennt vom
    Kunden-Login (/login). Zugangsdaten kommen ausschließlich aus der
    .env-Datei, niemals aus der Datenbank oder dem Frontend."""
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        if not _admin_login_configured():
            error = "Admin-Zugang ist serverseitig nicht konfiguriert (ADMIN_USERNAME/ADMIN_PASSWORD in .env setzen)."
        else:
            username = request.form.get("username") or ""
            password = request.form.get("password") or ""
            # Konstante Vergleichszeit gegen Timing-Angriffe.
            username_ok = secrets.compare_digest(username, ADMIN_USERNAME)
            password_ok = secrets.compare_digest(password, ADMIN_PASSWORD)
            if username_ok and password_ok:
                session["is_admin"] = True
                return redirect(url_for("admin_dashboard"))
            error = "Benutzername oder Passwort falsch."
    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    """Beendet die Admin-Sitzung. Bewusst kein login_required davor - ein
    Logout darf immer funktionieren, auch bei bereits abgelaufener Session."""
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    """Admin-Übersicht mit drei Tabs (die Suche läuft rein clientseitig per
    JS über die jeweils bereits gerenderte Tabelle, siehe
    admin_dashboard.html):

    - Offene Hardware-Bestellungen: bezahlt, Versandadresse liegt vor, aber
      noch nicht verschickt - mit Button zum Markieren als verschickt.
    - Bereits verschickte Hardware-Bestellungen.
    - Alle verkauften Produkte: digitale Lizenzen UND Hardware-Bestellungen
      zusammen, unabhängig vom Versandstatus - die Gesamtübersicht."""
    unshipped_orders = orders_db.get_unshipped_orders()
    shipped_orders = orders_db.get_shipped_orders()
    all_sales = _build_all_sales_overview()
    store_plugins = _list_store_plugins()

    return render_template(
        "admin_dashboard.html",
        products=PRODUCTS,
        unshipped_orders=unshipped_orders,
        shipped_orders=shipped_orders,
        all_sales=all_sales,
        store_plugins=store_plugins,
    )


def _build_all_sales_overview():
    """Baut die einheitliche Verkaufsübersicht für den Admin-Tab "Alle
    verkauften Produkte": vereint db.py's customer_licenses (digital) und
    orders_db.py's shipping_orders (physisch) - zwei unterschiedlich
    aufgebaute Tabellen aus zwei getrennten Datenbanken - zu einer einzigen,
    nach Datum sortierten Liste einheitlich aufgebauter dicts, damit das
    Template nicht zwischen zwei Zeilen-Formen unterscheiden muss."""
    sales = []
    for license_row in db.get_all_customer_licenses():
        product = PRODUCTS.get(license_row["product_id"])
        sales.append(
            {
                "type": "digital",
                "product_name": product["name"] if product else license_row["product_id"],
                "email": license_row["email"],
                "date": license_row["issued_at"],
                "detail": f"Hardware-ID: {license_row['hardware_id']}",
                "payment_provider": license_row["payment_provider"],
                "payment_reference": license_row["payment_reference"],
            }
        )
    for order_row in orders_db.get_all_orders():
        product = PRODUCTS.get(order_row["product_id"])
        versandstatus = (
            f"verschickt am {order_row['shipped_at'][:10]}" if order_row["shipped_at"] else "noch nicht verschickt"
        )
        sales.append(
            {
                "type": "physical",
                "product_name": product["name"] if product else order_row["product_id"],
                "email": order_row["email"],
                "date": order_row["ordered_at"],
                "detail": (
                    f"{order_row['full_name']}, {order_row['street_address']}, "
                    f"{order_row['postal_code']} {order_row['city']}, {order_row['country']} - {versandstatus}"
                ),
                "payment_provider": order_row["payment_provider"],
                "payment_reference": order_row["payment_reference"],
            }
        )
    sales.sort(key=lambda entry: entry["date"], reverse=True)
    return sales


@app.route("/admin/ship/<int:order_id>", methods=["POST"])
@admin_required
def admin_mark_shipped(order_id):
    """Markiert eine Hardware-Bestellung als verschickt und kehrt zum
    Admin-Dashboard zurück (Tab "Offene Bestellungen")."""
    orders_db.mark_order_shipped(order_id)
    return redirect(url_for("admin_dashboard", _anchor="unshipped"))


def _is_valid_mpy_bytes(content):
    """Strukturelle Plausibilitaetspruefung einer .mpy-Datei (nicht leer,
    beginnt mit dem MicroPython-.mpy-Magic-Byte) - siehe MPY_MAGIC_BYTE's
    Docstring-Kommentar dazu, warum eine echte Ausfuehrungspruefung hier
    bewusst NICHT stattfindet."""
    return len(content) > 0 and content[0] == MPY_MAGIC_BYTE


def _process_plugin_zip_upload(uploaded_file, uploaded_by=None, allow_reserved_names=False):
    """Gemeinsame Validierung/Entpack-Logik fuer Admin- UND Kunden-Uploads
    (siehe admin_plugins_upload()/plugins_upload() weiter unten) - entpackt
    eine Mod-ZIP-Datei FLACH (Unterordner in der ZIP werden ignoriert) nach
    static/plugins_store/<name>/. Die ZIP muss mindestens eine manifest.json
    und eine main.mpy enthalten (siehe source/plugin_manager.py's Manifest-
    Schema). <name> kommt aus manifest.json's "name"-Feld, nicht aus dem
    ZIP-Dateinamen. Ein erneuter Upload mit gleichem Namen ueberschreibt das
    bestehende Plugin - unabhaengig davon, wer es urspruenglich hochgeladen
    hat (kein Besitzrechte-/Freigabekonzept in dieser Version) - AUSSER bei
    den reservierten Standard-Mod-Namen (RESERVED_PLUGIN_NAMES), die nur ein
    Admin-Upload (allow_reserved_names=True) ueberschreiben darf, damit ein
    oeffentlicher Kunden-Upload niemals das mitgelieferte shooter-Plugin
    loeschen/ersetzen kann.

    WICHTIG: rohe .py-Dateien werden abgelehnt - der Store verteilt Mods
    ausschließlich als per mpy-cross vorkompilierte .mpy-Dateien (Quellcode-
    Schutz, gleiches Prinzip wie build_firmware.py's Firmware-Bundles). Jede
    .mpy-Datei wird zusaetzlich strukturell auf Plausibilitaet geprueft
    (siehe _is_valid_mpy_bytes()) - KEINE echte Ausfuehrung/Funktionspruefung,
    das waere ein Codeausfuehrungs-Sicherheitsrisiko auf dem Server.
    Kompilieren entweder manuell (mpy-cross) oder über
    tools/plugin_packager.py, das genau diesen Schritt automatisiert.

    uploaded_by (optional): E-Mail-Adresse bzw. Kennung des Hochladenden -
    wird in manifest.json als zusaetzliches Feld "uploaded_by" abgelegt
    (rein informativ, wird von source/plugin_manager.py ignoriert). Gibt
    (ok: bool, message: str) zurueck - ruft NIE redirect()/flash() selbst
    auf, das bleibt Sache der jeweiligen Route."""
    if uploaded_file is None or not uploaded_file.filename:
        return False, "Keine ZIP-Datei ausgewählt."

    if not uploaded_file.filename.lower().endswith(".zip"):
        return False, "Nur ZIP-Dateien werden akzeptiert."

    try:
        with zipfile.ZipFile(uploaded_file.stream) as archive:
            names = archive.namelist()

            for member in names:
                if not _is_safe_plugin_zip_member(member):
                    return False, f"Unsichere Datei in ZIP abgelehnt: {member}"

            py_files = [os.path.basename(name) for name in names if name.lower().endswith(".py")]
            if py_files:
                return False, (
                    "ZIP enthält unkompilierte .py-Dateien ("
                    + ", ".join(sorted(set(py_files)))
                    + "). Bitte zuerst mit mpy-cross zu .mpy kompilieren - manuell oder über "
                    "tools/plugin_packager.py."
                )

            manifest_candidates = [name for name in names if os.path.basename(name) == "manifest.json"]
            mpy_members = [name for name in names if os.path.basename(name).endswith(".mpy")]
            has_main = any(os.path.basename(name) == "main.mpy" for name in names)
            if not manifest_candidates or not has_main:
                return False, "ZIP muss manifest.json und main.mpy enthalten."

            for mpy_member in mpy_members:
                if not _is_valid_mpy_bytes(archive.read(mpy_member)):
                    return False, (
                        f"'{os.path.basename(mpy_member)}' ist keine gültige .mpy-Datei "
                        "(leer oder falsches Format) - bitte neu mit mpy-cross kompilieren."
                    )

            try:
                manifest_data = json.loads(archive.read(manifest_candidates[0]).decode("utf-8"))
            except Exception:
                return False, "manifest.json konnte nicht gelesen werden (ungültiges JSON)."

            plugin_name = str(manifest_data.get("name") or "").strip()
            if not plugin_name or not PLUGIN_NAME_PATTERN.match(plugin_name):
                return False, "manifest.json enthält keinen gültigen 'name' (nur Buchstaben/Zahlen/_/-)."

            if plugin_name in RESERVED_PLUGIN_NAMES and not allow_reserved_names:
                return False, (
                    f"'{plugin_name}' ist ein mitgeliefertes Standard-Plugin und kann nicht per "
                    "Kunden-Upload überschrieben werden. Bitte einen anderen Namen wählen."
                )

            target_dir = os.path.join(PLUGINS_STORE_DIR, plugin_name)
            os.makedirs(target_dir, exist_ok=True)

            for member in names:
                if member.endswith("/"):
                    continue
                filename = os.path.basename(member)
                if not filename:
                    continue
                with archive.open(member) as source_file, open(os.path.join(target_dir, filename), "wb") as dest_file:
                    dest_file.write(source_file.read())

            if uploaded_by:
                manifest_data["uploaded_by"] = uploaded_by
                manifest_data["uploaded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                with open(os.path.join(target_dir, "manifest.json"), "w", encoding="utf-8") as manifest_file:
                    json.dump(manifest_data, manifest_file)
    except zipfile.BadZipFile:
        return False, "Datei ist keine gültige ZIP-Datei."

    return True, f"Plugin '{plugin_name}' erfolgreich hochgeladen."


@app.route("/admin/plugins/upload", methods=["POST"])
@admin_required
def admin_plugins_upload():
    """Admin-Upload im Dashboard - siehe _process_plugin_zip_upload(). Als
    einzige Route darf der Admin auch reservierte Standard-Mod-Namen
    (z.B. 'shooter') ueberschreiben, etwa um eine neue offizielle Version
    bereitzustellen."""
    ok, message = _process_plugin_zip_upload(
        request.files.get("plugin_zip"), uploaded_by="admin", allow_reserved_names=True
    )
    flash(message, "success" if ok else "error")
    return redirect(url_for("admin_dashboard", _anchor="plugins"))


@app.route("/plugins/upload", methods=["POST"])
def plugins_upload():
    """Oeffentlicher Kunden-Upload auf der Store-Seite (/plugins) - jeder
    eingeloggte Kunde (normales Konto, siehe _current_account_email()) darf
    eigene Mods hochladen; kein separates Freigabe-/Moderationskonzept in
    dieser Version. Nicht eingeloggte Besucher werden zum Login
    umgeleitet."""
    account_email = _current_account_email()
    if not account_email:
        flash("Bitte zuerst einloggen, um ein Plugin hochzuladen.", "error")
        return redirect(url_for("login"))

    ok, message = _process_plugin_zip_upload(request.files.get("plugin_zip"), uploaded_by=account_email)
    flash(message, "success" if ok else "error")
    return redirect(url_for("plugins_store"))


if __name__ == "__main__":
    app.run(host="0.0.0.0",debug=True, port=5000)
