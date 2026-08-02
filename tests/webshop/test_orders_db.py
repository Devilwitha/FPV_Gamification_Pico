"""Tests fuer webshop/orders_db.py (Hardware-Bestellungen/Versandadressen)."""
import pytest


def _address(**overrides):
    address = {
        "full_name": "Max Muster",
        "street_address": "Musterstrasse 1",
        "address_line2": "",
        "postal_code": "8000",
        "city": "Zuerich",
        "country": "Schweiz",
        "phone": "",
    }
    address.update(overrides)
    return address


def test_init_db_is_idempotent(orders_db_module):
    orders_db_module.init_db()
    orders_db_module.init_db()


def test_add_and_get_pending_shipping_roundtrip(orders_db_module):
    pending_id = orders_db_module.add_pending_shipping("kunde@example.com", "hardware-lizenz", "stripe", "ref1")
    pending = orders_db_module.get_pending_shipping(pending_id)
    assert pending["email"] == "kunde@example.com"
    assert pending["product_id"] == "hardware-lizenz"


def test_get_pending_shipping_missing_returns_none(orders_db_module):
    assert orders_db_module.get_pending_shipping(999) is None
    assert orders_db_module.get_pending_shipping(None) is None


def test_payment_already_recorded(orders_db_module):
    assert orders_db_module.payment_already_recorded("stripe", "ref1") is False
    orders_db_module.add_pending_shipping("a@example.com", "p", "stripe", "ref1")
    assert orders_db_module.payment_already_recorded("stripe", "ref1") is True


def test_move_pending_to_order_transfers_address(orders_db_module):
    pending_id = orders_db_module.add_pending_shipping("kunde@example.com", "hardware-lizenz", "stripe", "ref1")
    order_id = orders_db_module.move_pending_to_order(pending_id, _address())

    assert orders_db_module.get_pending_shipping(pending_id) is None
    order = orders_db_module.get_order(order_id)
    assert order["full_name"] == "Max Muster"
    assert order["city"] == "Zuerich"
    assert order["shipped_at"] is None


def test_move_pending_to_order_missing_pending_raises(orders_db_module):
    with pytest.raises(ValueError):
        orders_db_module.move_pending_to_order(999, _address())


def test_move_pending_to_order_defaults_optional_fields(orders_db_module):
    pending_id = orders_db_module.add_pending_shipping("kunde@example.com", "p", "stripe", "ref1")
    address = _address()
    del address["address_line2"]
    del address["phone"]
    order_id = orders_db_module.move_pending_to_order(pending_id, address)
    order = orders_db_module.get_order(order_id)
    assert order["address_line2"] == ""
    assert order["phone"] == ""


def test_get_unshipped_and_shipped_orders_partition_correctly(orders_db_module):
    p1 = orders_db_module.add_pending_shipping("a@example.com", "p", "stripe", "ref1")
    order1 = orders_db_module.move_pending_to_order(p1, _address())
    p2 = orders_db_module.add_pending_shipping("b@example.com", "p", "stripe", "ref2")
    order2 = orders_db_module.move_pending_to_order(p2, _address())

    orders_db_module.mark_order_shipped(order1)

    unshipped = orders_db_module.get_unshipped_orders()
    shipped = orders_db_module.get_shipped_orders()
    assert [row["id"] for row in unshipped] == [order2]
    assert [row["id"] for row in shipped] == [order1]


def test_mark_order_shipped_is_idempotent(orders_db_module):
    pending_id = orders_db_module.add_pending_shipping("a@example.com", "p", "stripe", "ref1")
    order_id = orders_db_module.move_pending_to_order(pending_id, _address())

    orders_db_module.mark_order_shipped(order_id)
    first_shipped_at = orders_db_module.get_order(order_id)["shipped_at"]

    orders_db_module.mark_order_shipped(order_id)
    second_shipped_at = orders_db_module.get_order(order_id)["shipped_at"]

    assert first_shipped_at == second_shipped_at


def test_get_all_orders_spans_shipped_and_unshipped(orders_db_module):
    p1 = orders_db_module.add_pending_shipping("a@example.com", "p", "stripe", "ref1")
    order1 = orders_db_module.move_pending_to_order(p1, _address())
    orders_db_module.mark_order_shipped(order1)
    p2 = orders_db_module.add_pending_shipping("b@example.com", "p", "stripe", "ref2")
    orders_db_module.move_pending_to_order(p2, _address())

    all_orders = orders_db_module.get_all_orders()
    assert len(all_orders) == 2


def test_get_shipping_orders_for_email_is_case_insensitive(orders_db_module):
    pending_id = orders_db_module.add_pending_shipping("Kunde@Example.com", "p", "stripe", "ref1")
    orders_db_module.move_pending_to_order(pending_id, _address())
    orders = orders_db_module.get_shipping_orders_for_email("kunde@example.com")
    assert len(orders) == 1
