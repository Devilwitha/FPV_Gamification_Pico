"""Tests fuer webshop/db.py (Login-/Lizenz-Datenhaltung)."""
import pytest


def test_init_db_is_idempotent(db_module):
    db_module.init_db()
    db_module.init_db()  # zweiter Aufruf darf nicht scheitern (CREATE TABLE IF NOT EXISTS)


def test_add_and_get_pending_license_roundtrip(db_module):
    pending_id = db_module.add_pending_license("kunde@example.com", "software-lizenz", "stripe", "sess_123")
    pending = db_module.get_pending_license(pending_id)
    assert pending["email"] == "kunde@example.com"
    assert pending["product_id"] == "software-lizenz"
    assert pending["payment_provider"] == "stripe"
    assert pending["payment_reference"] == "sess_123"
    assert pending["created_at"]


def test_get_pending_license_missing_returns_none(db_module):
    assert db_module.get_pending_license(999) is None
    assert db_module.get_pending_license(None) is None


def test_get_pending_licenses_for_email_is_case_insensitive_and_ordered(db_module):
    db_module.add_pending_license("Kunde@Example.com", "a", "stripe", "ref1")
    db_module.add_pending_license("kunde@example.com", "b", "stripe", "ref2")
    rows = db_module.get_pending_licenses_for_email("KUNDE@example.com")
    assert [row["payment_reference"] for row in rows] == ["ref1", "ref2"]


def test_payment_already_recorded_detects_pending_and_customer_rows(db_module):
    assert db_module.payment_already_recorded("stripe", "ref1") is False
    db_module.add_pending_license("a@example.com", "p", "stripe", "ref1")
    assert db_module.payment_already_recorded("stripe", "ref1") is True

    pending_id = db_module.add_pending_license("b@example.com", "p", "stripe", "ref2")
    db_module.move_pending_to_customer(pending_id, "aabbccdd11223344", "aabbccdd11223344_20240101")
    assert db_module.payment_already_recorded("stripe", "ref2") is True


def test_move_pending_to_customer_transfers_row_atomically(db_module):
    pending_id = db_module.add_pending_license("kunde@example.com", "software-lizenz", "stripe", "ref1")
    db_module.move_pending_to_customer(pending_id, "aabbccdd11223344", "aabbccdd11223344_20240101")

    assert db_module.get_pending_license(pending_id) is None
    licenses = db_module.get_customer_licenses_for_email("kunde@example.com")
    assert len(licenses) == 1
    assert licenses[0]["hardware_id"] == "aabbccdd11223344"
    assert licenses[0]["license_filename"] == "aabbccdd11223344_20240101"
    assert licenses[0]["payment_reference"] == "ref1"


def test_move_pending_to_customer_missing_pending_raises(db_module):
    with pytest.raises(ValueError):
        db_module.move_pending_to_customer(999, "aabbccdd11223344", "x")


def test_get_customer_licenses_for_email_ordered_newest_first(db_module):
    p1 = db_module.add_pending_license("kunde@example.com", "a", "stripe", "ref1")
    db_module.move_pending_to_customer(p1, "id1", "file1")
    p2 = db_module.add_pending_license("kunde@example.com", "b", "stripe", "ref2")
    db_module.move_pending_to_customer(p2, "id2", "file2")

    licenses = db_module.get_customer_licenses_for_email("kunde@example.com")
    assert [row["license_filename"] for row in licenses] == ["file2", "file1"]


def test_get_all_customer_licenses_spans_all_emails(db_module):
    p1 = db_module.add_pending_license("a@example.com", "x", "stripe", "ref1")
    db_module.move_pending_to_customer(p1, "id1", "file1")
    p2 = db_module.add_pending_license("b@example.com", "x", "stripe", "ref2")
    db_module.move_pending_to_customer(p2, "id2", "file2")

    all_licenses = db_module.get_all_customer_licenses()
    assert {row["email"] for row in all_licenses} == {"a@example.com", "b@example.com"}


def test_get_customer_license_by_id(db_module):
    pending_id = db_module.add_pending_license("kunde@example.com", "x", "stripe", "ref1")
    license_id = db_module.move_pending_to_customer(pending_id, "id1", "file1")
    record = db_module.get_customer_license(license_id)
    assert record["email"] == "kunde@example.com"
    assert db_module.get_customer_license(999) is None
    assert db_module.get_customer_license(None) is None


def test_create_account_and_get_by_email(db_module):
    db_module.create_account(
        email="Kunde@Example.com",
        password_hash="hashed-value",
        full_name="Kunde Muster",
        address="Musterstrasse 1",
        phone="+41 79 000 00 00",
        country="Schweiz",
    )
    account = db_module.get_account_by_email("kunde@example.com")
    assert account is not None
    assert account["email"] == "Kunde@Example.com"
    assert account["password_hash"] == "hashed-value"
    assert account["full_name"] == "Kunde Muster"


def test_get_account_by_email_returns_none_when_missing(db_module):
    assert db_module.get_account_by_email("nobody@example.com") is None


def test_create_account_rejects_duplicate_email_case_insensitive(db_module):
    import sqlite3

    db_module.create_account(
        email="kunde@example.com",
        password_hash="hash1",
        full_name="A",
        address="A",
        phone="A",
        country="A",
    )
    with pytest.raises(sqlite3.IntegrityError):
        db_module.create_account(
            email="KUNDE@example.com",
            password_hash="hash2",
            full_name="B",
            address="B",
            phone="B",
            country="B",
        )
