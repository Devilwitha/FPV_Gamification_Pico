"""Tests fuer webshop/app.py (Flask-Routen, Checkout-Flow, Admin-Bereich).

Stripe/PayPal werden an der SDK-Grenze gemockt (stripe.checkout.Session.*
bzw. requests.post) - app.py's eigene Zahlungslogik bleibt dabei unveraendert
und wird vollstaendig durchlaufen; es wird nur verhindert, dass echte
Netzwerkaufrufe an Stripe/PayPal stattfinden. Fuer den kompletten
Kauf-bis-Lizenz-Flow ohne jeden Zahlungsanbieter wird app.py's eigener
DUMMY_MODE-Schalter genutzt (siehe webshop/CLAUDE.md).
"""
import importlib
import json
import sys
import types

import pytest


def test_index_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Code trifft Control" in resp.data


def test_webshop_home_preserves_product_introduction(client):
    resp = client.get("/webshop")
    assert resp.status_code == 200
    assert b"FPV Gamification Pico" in resp.data
    assert b"Smart Trick-Erkennung" in resp.data


def test_shop_page_lists_products(client):
    resp = client.get("/shop")
    assert resp.status_code == 200
    assert b"Software-Lizenz" in resp.data


def test_gatehill_install_page_loads(client):
    resp = client.get("/gatehill-install")
    assert resp.status_code == 200


def test_checkout_page_for_known_product(client):
    resp = client.get("/checkout/software-lizenz")
    assert resp.status_code == 200


def test_checkout_page_for_unknown_product_returns_404(client):
    resp = client.get("/checkout/does-not-exist")
    assert resp.status_code == 404


def test_format_price_filter(webshop_app):
    assert webshop_app.format_price(1995) == "19.95 CHF"
    assert webshop_app.format_price(0) == "0.00 CHF"
    assert webshop_app.format_price(100000) == "1'000.00 CHF"


def test_hardware_id_pattern():
    app_module = importlib.import_module("app")
    pattern = app_module.HARDWARE_ID_PATTERN
    assert pattern.match("aabbccdd11223344")
    assert not pattern.match("not-hex!!")
    assert not pattern.match("abc")  # zu kurz


def test_https_config_enables_secure_session_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBSHOP_DB_PATH", str(tmp_path / "webshop.db"))
    monkeypatch.setenv("WEBSHOP_ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DOMAIN_URL", "https://shop.example.com")
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("TRUST_PROXY_COUNT", raising=False)
    # app.py's load_dotenv() would otherwise re-fill deleted vars from a real
    # local webshop/.env (only sets vars still absent from os.environ) and
    # make this test depend on the developer's own deployment config.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)

    for module_name in ("app", "db", "orders_db"):
        sys.modules.pop(module_name, None)

    app_module = importlib.import_module("app")

    assert app_module.HTTPS_ENABLED is True
    assert app_module.app.config["SESSION_COOKIE_SECURE"] is True
    assert app_module.app.config["PREFERRED_URL_SCHEME"] == "https"


def test_trust_proxy_count_wraps_wsgi_app(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBSHOP_DB_PATH", str(tmp_path / "webshop.db"))
    monkeypatch.setenv("WEBSHOP_ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DOMAIN_URL", "https://shop.example.com")
    monkeypatch.setenv("TRUST_PROXY_COUNT", "1")

    for module_name in ("app", "db", "orders_db"):
        sys.modules.pop(module_name, None)

    app_module = importlib.import_module("app")

    assert type(app_module.app.wsgi_app).__name__ == "ProxyFix"


def test_http_config_keeps_secure_cookie_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBSHOP_DB_PATH", str(tmp_path / "webshop.db"))
    monkeypatch.setenv("WEBSHOP_ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("DOMAIN_URL", "http://localhost:5000")
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("TRUST_PROXY_COUNT", raising=False)
    # see test_https_config_enables_secure_session_cookie() - avoid a real
    # local webshop/.env re-filling the deleted SESSION_COOKIE_SECURE var.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)

    for module_name in ("app", "db", "orders_db"):
        sys.modules.pop(module_name, None)

    app_module = importlib.import_module("app")

    assert app_module.HTTPS_ENABLED is False
    assert app_module.app.config["SESSION_COOKIE_SECURE"] is False
    assert app_module.app.config["PREFERRED_URL_SCHEME"] == "http"


# ==================== Dummy-Kauf-Flow ====================

def test_dummy_purchase_disabled_returns_403(client, webshop_app, monkeypatch):
    monkeypatch.setattr(webshop_app, "DUMMY_MODE", False)
    resp = client.post("/api/dummy/create-purchase", json={"product_id": "software-lizenz", "email": "a@b.com"})
    assert resp.status_code == 403


def test_dummy_purchase_unknown_product_returns_404(client):
    resp = client.post("/api/dummy/create-purchase", json={"product_id": "nope", "email": "a@b.com"})
    assert resp.status_code == 404


def test_dummy_purchase_invalid_email_returns_400(client):
    resp = client.post("/api/dummy/create-purchase", json={"product_id": "software-lizenz", "email": "not-an-email"})
    assert resp.status_code == 400


def test_dummy_purchase_digital_creates_pending_license(client, webshop_app):
    resp = client.post(
        "/api/dummy/create-purchase", json={"product_id": "software-lizenz", "email": "kunde@example.com"}
    )
    assert resp.status_code == 200
    assert "redirect_url" in resp.get_json()

    pending = webshop_app.db.get_pending_licenses_for_email("kunde@example.com")
    assert len(pending) == 1
    assert pending[0]["payment_provider"] == "dummy"


def test_dummy_purchase_physical_creates_pending_shipping(client, webshop_app):
    resp = client.post(
        "/api/dummy/create-purchase", json={"product_id": "hardware-lizenz", "email": "kunde@example.com"}
    )
    assert resp.status_code == 200
    pending = webshop_app.orders_db.get_pending_shipping_for_email("kunde@example.com")
    assert len(pending) == 1


def test_dummy_purchase_free_product_creates_no_pending_rows(client, webshop_app):
    resp = client.post(
        "/api/dummy/create-purchase", json={"product_id": "gatehill-gratis", "email": "kunde@example.com"}
    )
    assert resp.status_code == 200
    assert webshop_app.db.get_pending_licenses_for_email("kunde@example.com") == []
    assert webshop_app.orders_db.get_pending_shipping_for_email("kunde@example.com") == []


def test_dummy_purchase_is_idempotent_for_same_reference(client, webshop_app):
    # _record_valid_purchase() prueft payment_already_recorded() - hier durch
    # doppeltes Verschieben in customer_licenses simuliert.
    pending_id = webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "DUMMY-1")
    webshop_app.db.move_pending_to_customer(pending_id, "aabbccdd11223344", "file1")
    webshop_app._record_valid_purchase(webshop_app.PRODUCTS["software-lizenz"], "kunde@example.com", "dummy", "DUMMY-1")
    # Da payment_already_recorded() True liefert, darf KEIN zweiter pending-Eintrag entstehen.
    assert webshop_app.db.get_pending_licenses_for_email("kunde@example.com") == []


# ==================== success / cancel ====================

def test_success_redirects_to_account_when_pending_license_exists(client):
    with client.session_transaction() as sess:
        sess["checkout_product_id"] = "software-lizenz"
        sess["checkout_pending_id"] = 1
    resp = client.get("/success")
    assert resp.status_code == 302
    assert "/account" in resp.headers["Location"]


def test_success_shows_generic_page_without_pending(client):
    resp = client.get("/success")
    assert resp.status_code == 200


def test_cancel_page_loads(client):
    resp = client.get("/cancel")
    assert resp.status_code == 200


# ==================== license-setup / api/license/create ====================

def test_license_setup_without_pending_returns_403(client):
    resp = client.get("/license-setup")
    assert resp.status_code == 403


def test_license_setup_with_authorized_pending_shows_form(client, webshop_app):
    pending_id = webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "ref1")
    with client.session_transaction() as sess:
        sess["checkout_pending_id"] = pending_id
    resp = client.get("/license-setup")
    assert resp.status_code == 200


def test_api_license_create_without_pending_returns_403(client):
    resp = client.post("/api/license/create", json={"pending_id": 999, "hardware_id": "aabbccdd11223344"})
    assert resp.status_code == 403


def test_api_license_create_unauthorized_pending_returns_403(client, webshop_app):
    pending_id = webshop_app.db.add_pending_license("other@example.com", "software-lizenz", "dummy", "ref1")
    # Keine Session, die pending_id oder E-Mail zuordnen kann -> nicht autorisiert.
    resp = client.post("/api/license/create", json={"pending_id": pending_id, "hardware_id": "aabbccdd11223344"})
    assert resp.status_code == 403


def test_api_license_create_missing_keypair_returns_500(client, webshop_app):
    pending_id = webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "ref1")
    with client.session_transaction() as sess:
        sess["checkout_pending_id"] = pending_id
    resp = client.post("/api/license/create", json={"pending_id": pending_id, "hardware_id": "aabbccdd11223344"})
    assert resp.status_code == 500
    assert "Schlüsselpaar" in resp.get_json()["error"]


def test_api_license_create_invalid_hardware_id_returns_400(client, webshop_app, keypair):
    pending_id = webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "ref1")
    with client.session_transaction() as sess:
        sess["checkout_pending_id"] = pending_id
    resp = client.post("/api/license/create", json={"pending_id": pending_id, "hardware_id": "not-valid!"})
    assert resp.status_code == 400


def test_api_license_create_success_issues_signed_license(client, webshop_app, keypair):
    import license_verifier

    pending_id = webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "ref1")
    with client.session_transaction() as sess:
        sess["checkout_pending_id"] = pending_id

    resp = client.post("/api/license/create", json={"pending_id": pending_id, "hardware_id": "aabbccdd11223344"})
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"].startswith("attachment")

    parsed = license_verifier.parse_license_text(resp.data.decode("utf-8"))
    assert parsed["fields"]["hardware_id"] == "aabbccdd11223344"

    # Bestellung ist jetzt "gewandert": nicht mehr offen, dafuer als Lizenz ausgestellt.
    assert webshop_app.db.get_pending_license(pending_id) is None
    licenses = webshop_app.db.get_customer_licenses_for_email("kunde@example.com")
    assert len(licenses) == 1


def test_license_setup_public_key_missing_returns_404(client):
    resp = client.get("/license-setup/public-key.pem")
    assert resp.status_code == 404


def test_license_setup_public_key_served_when_present(client, webshop_app, keypair):
    resp = client.get("/license-setup/public-key.pem")
    assert resp.status_code == 200


# ==================== shipping-setup ====================

def test_shipping_setup_without_pending_returns_403(client):
    resp = client.get("/shipping-setup")
    assert resp.status_code == 403


def test_shipping_setup_post_missing_fields_shows_error(client, webshop_app):
    pending_id = webshop_app.orders_db.add_pending_shipping("kunde@example.com", "hardware-lizenz", "dummy", "ref1")
    with client.session_transaction() as sess:
        sess["checkout_pending_shipping_id"] = pending_id
    resp = client.post("/shipping-setup", data={"full_name": "Max"})
    assert resp.status_code == 200
    assert "Pflichtfelder" in resp.data.decode("utf-8")


def test_shipping_setup_post_success_moves_to_order_and_redirects(client, webshop_app):
    pending_id = webshop_app.orders_db.add_pending_shipping("kunde@example.com", "hardware-lizenz", "dummy", "ref1")
    with client.session_transaction() as sess:
        sess["checkout_pending_shipping_id"] = pending_id

    resp = client.post(
        "/shipping-setup",
        data={
            "full_name": "Max Muster",
            "street_address": "Musterstrasse 1",
            "postal_code": "8000",
            "city": "Zuerich",
            "country": "Schweiz",
        },
    )
    assert resp.status_code == 302
    assert "/account" in resp.headers["Location"]
    assert webshop_app.orders_db.get_pending_shipping(pending_id) is None
    orders = webshop_app.orders_db.get_shipping_orders_for_email("kunde@example.com")
    assert len(orders) == 1

    with client.session_transaction() as sess:
        assert sess.get("account_email") == "kunde@example.com"
        assert "checkout_pending_shipping_id" not in sess


# ==================== login / logout / account ====================

def _register(client, email="kunde@example.com", password="supersecret", **overrides):
    data = {
        "email": email,
        "password": password,
        "full_name": "Kunde Muster",
        "country": "Schweiz",
    }
    data.update(overrides)
    return client.post("/register", data=data)


def test_register_creates_account_and_logs_in(client, webshop_app):
    resp = _register(client)
    assert resp.status_code == 302
    assert "/account" in resp.headers["Location"]

    account = webshop_app.db.get_account_by_email("kunde@example.com")
    assert account is not None
    assert account["full_name"] == "Kunde Muster"
    with client.session_transaction() as sess:
        assert sess.get("account_email") == "kunde@example.com"


def test_register_rejects_invalid_email(client):
    resp = _register(client, email="not-an-email")
    assert b"g\xc3\xbcltige E-Mail" in resp.data


def test_register_rejects_short_password(client):
    resp = _register(client, password="short")
    assert "mindestens 8 Zeichen" in resp.data.decode("utf-8")


def test_register_rejects_missing_profile_fields(client):
    resp = _register(client, full_name="")
    assert "vollständig ausfüllen" in resp.data.decode("utf-8")


def test_register_rejects_duplicate_email(client):
    _register(client)
    resp = _register(client)
    assert "bereits ein Konto" in resp.data.decode("utf-8")


def test_login_invalid_email_shows_error(client):
    resp = client.post("/login", data={"email": "not-an-email", "password": "whatever1"})
    assert "falsch" in resp.data.decode("utf-8")


def test_login_unknown_email_shows_error(client):
    resp = client.post("/login", data={"email": "unknown@example.com", "password": "whatever1"})
    assert resp.status_code == 200
    assert "falsch" in resp.data.decode("utf-8")


def test_login_wrong_password_shows_error(client):
    _register(client)
    resp = client.post("/login", data={"email": "kunde@example.com", "password": "wrong-password"})
    assert resp.status_code == 200
    assert "falsch" in resp.data.decode("utf-8")


def test_login_known_email_redirects_to_account(client, webshop_app):
    _register(client)
    client.get("/logout")
    resp = client.post("/login", data={"email": "kunde@example.com", "password": "supersecret"})
    assert resp.status_code == 302
    assert "/account" in resp.headers["Location"]


def test_logout_clears_session(client):
    with client.session_transaction() as sess:
        sess["account_email"] = "kunde@example.com"
    resp = client.get("/logout")
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "account_email" not in sess


def test_account_redirects_to_login_when_not_authenticated(client):
    resp = client.get("/account")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_account_shows_pending_and_licenses_when_authenticated(client, webshop_app):
    webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "ref1")
    with client.session_transaction() as sess:
        sess["account_email"] = "kunde@example.com"
    resp = client.get("/account")
    assert resp.status_code == 200


def test_account_download_license_requires_login(client):
    resp = client.get("/account/download/1")
    assert resp.status_code == 302


def test_account_download_license_wrong_owner_returns_404(client, webshop_app):
    pending_id = webshop_app.db.add_pending_license("owner@example.com", "software-lizenz", "dummy", "ref1")
    license_id = webshop_app.db.move_pending_to_customer(pending_id, "aabbccdd11223344", "file1")
    with client.session_transaction() as sess:
        sess["account_email"] = "someone-else@example.com"
    resp = client.get(f"/account/download/{license_id}")
    assert resp.status_code == 404


def test_account_download_license_missing_file_returns_404(client, webshop_app):
    pending_id = webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "ref1")
    license_id = webshop_app.db.move_pending_to_customer(pending_id, "aabbccdd11223344", "file-that-does-not-exist")
    with client.session_transaction() as sess:
        sess["account_email"] = "kunde@example.com"
    resp = client.get(f"/account/download/{license_id}")
    assert resp.status_code == 404


def test_account_download_license_success(client, webshop_app, keypair, tmp_path):
    import os

    pending_id = webshop_app.db.add_pending_license("kunde@example.com", "software-lizenz", "dummy", "ref1")
    license_id = webshop_app.db.move_pending_to_customer(pending_id, "aabbccdd11223344", "myfile")
    os.makedirs(webshop_app.LICENSES_DIR, exist_ok=True)
    with open(os.path.join(webshop_app.LICENSES_DIR, "myfile.lic"), "w") as f:
        f.write("license-content")

    with client.session_transaction() as sess:
        sess["account_email"] = "kunde@example.com"
    resp = client.get(f"/account/download/{license_id}")
    assert resp.status_code == 200
    assert resp.data == b"license-content"


# ==================== Admin-Bereich ====================

def test_admin_dashboard_redirects_when_not_logged_in(client):
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_admin_login_not_configured(client, webshop_app, monkeypatch):
    monkeypatch.setattr(webshop_app, "ADMIN_USERNAME", "")
    monkeypatch.setattr(webshop_app, "ADMIN_PASSWORD", "")
    resp = client.post("/admin/login", data={"username": "admin", "password": "adminpass"})
    assert "serverseitig nicht konfiguriert" in resp.data.decode("utf-8")


def test_admin_login_wrong_credentials(client):
    resp = client.post("/admin/login", data={"username": "admin", "password": "wrong"})
    assert "falsch" in resp.data.decode("utf-8")


def test_admin_login_success_grants_access_to_dashboard(client):
    resp = client.post("/admin/login", data={"username": "admin", "password": "adminpass"})
    assert resp.status_code == 302
    assert "/admin" in resp.headers["Location"]

    dashboard = client.get("/admin")
    assert dashboard.status_code == 200


def test_admin_logout_clears_admin_session(client):
    client.post("/admin/login", data={"username": "admin", "password": "adminpass"})
    resp = client.get("/admin/logout")
    assert resp.status_code == 302
    assert client.get("/admin").status_code == 302


def test_admin_mark_shipped(client, webshop_app):
    pending_id = webshop_app.orders_db.add_pending_shipping("kunde@example.com", "hardware-lizenz", "dummy", "ref1")
    order_id = webshop_app.orders_db.move_pending_to_order(
        pending_id,
        {"full_name": "Max Muster", "street_address": "Strasse 1", "postal_code": "8000", "city": "Zuerich", "country": "CH"},
    )
    client.post("/admin/login", data={"username": "admin", "password": "adminpass"})
    resp = client.post(f"/admin/ship/{order_id}")
    assert resp.status_code == 302
    assert webshop_app.orders_db.get_order(order_id)["shipped_at"] is not None


def test_admin_dashboard_requires_login_for_mark_shipped(client, webshop_app):
    resp = client.post("/admin/ship/1")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_build_all_sales_overview_merges_and_sorts(webshop_app):
    pending_id = webshop_app.db.add_pending_license("a@example.com", "software-lizenz", "dummy", "ref1")
    webshop_app.db.move_pending_to_customer(pending_id, "aabbccdd11223344", "file1")

    pending_shipping_id = webshop_app.orders_db.add_pending_shipping("b@example.com", "hardware-lizenz", "dummy", "ref2")
    webshop_app.orders_db.move_pending_to_order(
        pending_shipping_id,
        {"full_name": "Max Muster", "street_address": "Strasse 1", "postal_code": "8000", "city": "Zuerich", "country": "CH"},
    )

    sales = webshop_app._build_all_sales_overview()
    assert {entry["type"] for entry in sales} == {"digital", "physical"}
    assert len(sales) == 2


# ==================== Stripe / PayPal (an der SDK-Grenze gemockt) ====================

def test_stripe_checkout_unknown_product_returns_404(client):
    resp = client.post("/api/stripe/create-checkout-session", json={"product_id": "nope", "email": "a@b.com"})
    assert resp.status_code == 404


def test_stripe_checkout_invalid_email_returns_400(client):
    resp = client.post(
        "/api/stripe/create-checkout-session", json={"product_id": "software-lizenz", "email": "invalid"}
    )
    assert resp.status_code == 400


def test_stripe_checkout_success_returns_checkout_url(client, webshop_app, monkeypatch):
    created = {}

    class FakeSession:
        url = "https://checkout.stripe.com/fake-session"

    def fake_create(**kwargs):
        created.update(kwargs)
        return FakeSession()

    monkeypatch.setattr(webshop_app.stripe.checkout.Session, "create", staticmethod(fake_create))

    resp = client.post(
        "/api/stripe/create-checkout-session",
        json={"product_id": "software-lizenz", "email": "kunde@example.com"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["checkout_url"] == "https://checkout.stripe.com/fake-session"
    assert created["customer_email"] == "kunde@example.com"

    with client.session_transaction() as sess:
        assert sess["checkout_product_id"] == "software-lizenz"


def test_stripe_checkout_stripe_error_returns_400(client, webshop_app, monkeypatch):
    def fake_create(**kwargs):
        raise webshop_app.stripe.error.StripeError("boom")

    monkeypatch.setattr(webshop_app.stripe.checkout.Session, "create", staticmethod(fake_create))
    resp = client.post(
        "/api/stripe/create-checkout-session",
        json={"product_id": "software-lizenz", "email": "kunde@example.com"},
    )
    assert resp.status_code == 400


def test_success_route_verifies_stripe_session_and_records_purchase(client, webshop_app, monkeypatch):
    fake_session = types.SimpleNamespace(
        payment_status="paid",
        metadata={"product_id": "software-lizenz", "email": "kunde@example.com"},
    )
    monkeypatch.setattr(webshop_app.stripe.checkout.Session, "retrieve", staticmethod(lambda sid: fake_session))

    resp = client.get("/success?session_id=sess_abc")
    assert resp.status_code == 302  # digitales Produkt -> Weiterleitung zu /account
    pending = webshop_app.db.get_pending_licenses_for_email("kunde@example.com")
    assert len(pending) == 1
    assert pending[0]["payment_reference"] == "sess_abc"


def test_get_paypal_access_token_uses_client_credentials(webshop_app, monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "fake-token"}

    def fake_post(url, headers=None, data=None, auth=None, timeout=None):
        captured["url"] = url
        captured["auth"] = auth
        captured["data"] = data
        return FakeResponse()

    monkeypatch.setattr(webshop_app.requests, "post", fake_post)
    token = webshop_app.get_paypal_access_token()
    assert token == "fake-token"
    assert captured["auth"] == (webshop_app.PAYPAL_CLIENT_ID, webshop_app.PAYPAL_CLIENT_SECRET)
    assert captured["data"] == {"grant_type": "client_credentials"}


def test_paypal_create_order_success(client, webshop_app, monkeypatch):
    monkeypatch.setattr(webshop_app, "get_paypal_access_token", lambda: "fake-token")

    class FakeResponse:
        status_code = 201

        def json(self):
            return {"id": "PAYPAL-ORDER-1"}

    monkeypatch.setattr(webshop_app.requests, "post", lambda *a, **kw: FakeResponse())

    resp = client.post(
        "/api/paypal/create-order", json={"product_id": "software-lizenz", "email": "kunde@example.com"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["id"] == "PAYPAL-ORDER-1"


def test_paypal_create_order_unknown_product(client, webshop_app, monkeypatch):
    monkeypatch.setattr(webshop_app, "get_paypal_access_token", lambda: "fake-token")
    resp = client.post("/api/paypal/create-order", json={"product_id": "nope", "email": "a@b.com"})
    assert resp.status_code == 404


def test_paypal_create_order_auth_failure_returns_502(client, webshop_app, monkeypatch):
    def raise_error():
        raise webshop_app.requests.RequestException("no network")

    monkeypatch.setattr(webshop_app, "get_paypal_access_token", raise_error)
    resp = client.post(
        "/api/paypal/create-order", json={"product_id": "software-lizenz", "email": "kunde@example.com"}
    )
    assert resp.status_code == 502


def test_paypal_capture_order_completed_records_purchase(client, webshop_app, monkeypatch):
    monkeypatch.setattr(webshop_app, "get_paypal_access_token", lambda: "fake-token")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"status": "COMPLETED"}

    monkeypatch.setattr(webshop_app.requests, "post", lambda *a, **kw: FakeResponse())

    with client.session_transaction() as sess:
        sess["checkout_product_id"] = "software-lizenz"
        sess["checkout_email"] = "kunde@example.com"

    resp = client.post("/api/paypal/capture-order/PAYPAL-ORDER-1")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "COMPLETED"
    pending = webshop_app.db.get_pending_licenses_for_email("kunde@example.com")
    assert len(pending) == 1
    assert pending[0]["payment_reference"] == "PAYPAL-ORDER-1"


def test_paypal_capture_order_not_completed_does_not_record(client, webshop_app, monkeypatch):
    monkeypatch.setattr(webshop_app, "get_paypal_access_token", lambda: "fake-token")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"status": "FAILED"}

    monkeypatch.setattr(webshop_app.requests, "post", lambda *a, **kw: FakeResponse())

    with client.session_transaction() as sess:
        sess["checkout_product_id"] = "software-lizenz"
        sess["checkout_email"] = "kunde@example.com"

    resp = client.post("/api/paypal/capture-order/PAYPAL-ORDER-1")
    assert resp.status_code == 200
    assert webshop_app.db.get_pending_licenses_for_email("kunde@example.com") == []
