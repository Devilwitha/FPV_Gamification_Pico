"""Webshop-Anwendung mit echter Stripe- und PayPal-Zahlungsanbindung."""

import json
import os
import re
import secrets
import sys
import time
from datetime import datetime
from functools import wraps

import requests
import stripe
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, session, url_for

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
            "Infection-Modus (BLE) und OTA-Updates. Sofortige digitale "
            "Freischaltung nach dem Kauf."
        ),
        "type": "digital",
        "price_cents": 195,
        "currency": "chf",
        "image": "bilder/shop/lizenz/Gemini_Generated_Image_f7pr0tf7pr0tf7pr.png",
    },
    "hardware-lizenz": {
        "id": "hardware-lizenz",
        "name": "Hardware + Lizenz",
        "description": (
            "Raspberry Pi Pico inklusive vorinstallierter FPV Gamification "
            "Firmware. Einfach per CRSF-UART am Flight Controller anschließen "
            "(GND, CRSF TX auf GP1, optional 5V) und direkt lossfliegen."
        ),
        "type": "physical",
        "price_cents": 1995,
        "currency": "chf",
        "image": "bilder/shop/Hardware/Gemini_Generated_Image_8jui9u8jui9u8jui.png",
    },
}


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


def _has_any_order_for_email(email):
    """Prüft, ob zu ``email`` irgendein Datensatz existiert - egal ob
    offene/ausgestellte Lizenz (db.py, webshop.db) oder offene/abgeschlossene
    Hardware-Bestellung (orders_db.py, komplett getrennte orders.db). Beide
    Datenbanken müssen hier geprüft werden, sonst findet ein Kunde mit einer
    reinen Hardware-Bestellung (noch ohne Lizenz-Kauf) beim Login nichts."""
    return bool(
        db.get_pending_licenses_for_email(email)
        or db.get_customer_licenses_for_email(email)
        or orders_db.get_pending_shipping_for_email(email)
        or orders_db.get_shipping_orders_for_email(email)
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login-Seite für den Konto-Bereich: die Eingabe der E-Mail-Adresse ist
    hier bewusst bereits der gesamte Login-Vorgang (kein zusätzlicher
    Bestätigungslink/-code per Mail) - Voraussetzung ist lediglich, dass zu
    der Adresse mindestens eine offene Bestellung oder bereits ausgestellte
    Lizenz bzw. Hardware-Bestellung existiert."""
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        if "@" not in email:
            error = "Bitte eine gültige E-Mail-Adresse eingeben."
        elif _has_any_order_for_email(email):
            session["account_email"] = email
            return redirect(url_for("account"))
        else:
            error = "Für diese E-Mail-Adresse wurden keine Bestellungen gefunden."
    return render_template("login.html", error=error)


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

    return render_template(
        "admin_dashboard.html",
        products=PRODUCTS,
        unshipped_orders=unshipped_orders,
        shipped_orders=shipped_orders,
        all_sales=all_sales,
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


if __name__ == "__main__":
    app.run(host="0.0.0.0",debug=True, port=5000)
