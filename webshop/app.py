"""Webshop-Anwendung mit echter Stripe- und PayPal-Zahlungsanbindung."""

import json
import os
import re
import sys
import time
from datetime import datetime

import requests
import stripe
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, session, url_for

load_dotenv()

# DUMMY_MODE (Default: aus) - siehe webshop/CLAUDE.md Abschnitt "Ausnahme
# (lokaler Dev-Schalter)": schaltet AUSSCHLIESSLICH lokal/zu Testzwecken einen
# zusaetzlichen "Dummy-Kauf simulieren"-Button frei, der Stripe/PayPal komplett
# umgeht. Ruehrt die echten Zahlungspfade nicht an und ist standardmaessig aus.
DUMMY_MODE = os.environ.get("DUMMY_MODE", "false").strip().lower() == "true"

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
        "price_cents": 4999,
        "currency": "eur",
        "image": "https://via.placeholder.com/400x300?text=Software-Lizenz",
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
        "price_cents": 19999,
        "currency": "eur",
        "image": "https://via.placeholder.com/400x300?text=Hardware+Lizenz",
    },
}


def format_price(price_cents):
    """Formatiert einen Cent-Betrag als deutsche Euro-Zeichenkette."""
    euro_betrag = price_cents / 100
    formatiert = f"{euro_betrag:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatiert} €"


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
    """Checkout-Seite für ein bestimmtes Produkt mit Auswahl der Zahlungsmethode."""
    product = PRODUCTS.get(product_id)
    if product is None:
        return render_template("cancel.html", message="Dieses Produkt existiert nicht."), 404
    return render_template(
        "checkout.html",
        product=product,
        stripe_publishable_key=STRIPE_PUBLISHABLE_KEY,
        paypal_client_id=PAYPAL_CLIENT_ID,
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

    return jsonify(response.json())


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

    order_id = f"DUMMY-{int(time.time())}"
    return jsonify({"redirect_url": url_for("success", order_id=order_id)})


@app.route("/success")
def success():
    """Erfolgsseite nach abgeschlossener Zahlung.

    Digitale Produkte (Software-Lizenz) werden direkt zur Lizenz-Einrichtung
    weitergeleitet - Grundlage dafür ist NICHT der (client-kontrollierbare)
    Query-String, sondern die serverseitige Flask-Session, die bereits beim
    Start des Checkouts gesetzt wurde (siehe stripe_create_checkout_session()/
    paypal_create_order()/dummy_create_purchase())."""
    session_id = request.args.get("session_id")
    order_id = request.args.get("order_id")

    product = PRODUCTS.get(session.get("checkout_product_id"))
    if product is not None and product["type"] == "digital" and session.get("checkout_email"):
        return redirect(url_for("license_setup"))

    return render_template("success.html", session_id=session_id, order_id=order_id)


@app.route("/cancel")
def cancel():
    """Abbruchseite bei nicht abgeschlossener Zahlung."""
    return render_template("cancel.html", message="Der Bezahlvorgang wurde abgebrochen.")


@app.route("/license-setup")
def license_setup():
    """Anleitungsseite nach Kauf einer Software-Lizenz: MicroPython-Installation,
    Thonny-Skript zum Auslesen der Hardware-ID, Eingabefeld zur Lizenzerzeugung."""
    product = PRODUCTS.get(session.get("checkout_product_id"))
    email = session.get("checkout_email")
    if product is None or product["type"] != "digital" or not email:
        return render_template(
            "cancel.html",
            message="Keine gültige Lizenz-Bestellung gefunden. Bitte zuerst im Shop kaufen.",
        ), 403
    return render_template("license_setup.html", product=product, email=email)


@app.route("/api/license/create", methods=["POST"])
def api_license_create():
    """Signiert eine license.lic für die vom Kunden per Thonny ausgelesene
    Hardware-ID und liefert sie zum Download. Der Kunde hat keine serielle
    Verbindung zu diesem Server (anders als beim GUI-Flow in
    tools/build_firmware.py) - daher wird die Hardware-ID manuell abgefragt
    statt sie automatisiert vom Gerät zu lesen."""
    product = PRODUCTS.get(session.get("checkout_product_id"))
    email = session.get("checkout_email")
    if product is None or product["type"] != "digital" or not email:
        return jsonify({"error": "Keine gültige Lizenz-Bestellung gefunden. Bitte zuerst im Shop kaufen."}), 403

    if license_generator is None:
        return jsonify({"error": "Lizenzsystem serverseitig nicht verfügbar (Paket 'cryptography' fehlt)."}), 500

    if not (os.path.isfile(PRIVATE_KEY_PATH) and os.path.isfile(PUBLIC_KEY_PATH)):
        return jsonify({"error": "RSA-Schlüsselpaar für das Lizenzsystem wurde serverseitig noch nicht erzeugt."}), 500

    data = request.get_json(silent=True) or {}
    hardware_id = (data.get("hardware_id") or "").strip().lower()
    if not HARDWARE_ID_PATTERN.match(hardware_id):
        return jsonify(
            {"error": "Ungültige Hardware-ID. Bitte die Ausgabe des Thonny-Skripts unverändert einfügen."}
        ), 400

    try:
        license_content = license_generator.sign_license_from_key_file(
            PRIVATE_KEY_PATH, hardware_id, email,
        )
    except Exception as fehler:
        return jsonify({"error": f"Lizenz konnte nicht erzeugt werden: {fehler}"}), 500

    _save_license_record(hardware_id, email, license_content)

    # Session zurücksetzen: verhindert, dass mit derselben Bestellung mehrfach
    # unbemerkt weitere Lizenzen für andere Hardware-IDs erzeugt werden.
    session.pop("checkout_product_id", None)
    session.pop("checkout_email", None)

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


def _save_license_record(hardware_id, email, license_content):
    """Legt jede über den Webshop ausgestellte Lizenz dauerhaft unter
    LICENSES_DIR ab (license.lic + Begleit-JSON mit Hardware-ID/E-Mail/
    Zeitpunkt) - analog zu build_firmware.py's save_license_record(), damit
    beide Ausgabewege (seriell per GUI, digital per Webshop) im selben Ordner
    nachvollziehbar bleiben."""
    os.makedirs(LICENSES_DIR, exist_ok=True)

    issued_date = datetime.now().strftime("%Y%m%d")
    safe_hardware_id = "".join(c for c in hardware_id if c.isalnum()) or "unknown"

    # Reine Datums-Zeitstempel (keine Uhrzeit) sind nicht mehr zwingend
    # eindeutig, wenn fuer dieselbe Hardware-ID am selben Tag mehrfach eine
    # Lizenz ausgestellt wird (z.B. erneuter Kaufversuch) - daher bei Bedarf
    # einen laufenden Suffix anhaengen, statt eine bestehende Datei stumm zu
    # ueberschreiben.
    base_name = f"{safe_hardware_id}_{issued_date}"
    suffix = 1
    while os.path.exists(os.path.join(LICENSES_DIR, base_name + ".lic")):
        suffix += 1
        base_name = f"{safe_hardware_id}_{issued_date}_{suffix}"

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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
