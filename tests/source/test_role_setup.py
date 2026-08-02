"""Tests fuer source/role_setup.py.

role_setup.py ruft am Modul-Ende bedingungslos `run()` -> `asyncio.run(
main_async())` auf, was ohne Gegenmassnahme die komplette Test-Session
haengen liesse (main_async() ist ein `while True`). Der `import_entry_module`-
Fixture aus conftest.py importiert das Modul frisch, waehrend asyncio.run()
kurzzeitig durch eine Variante ersetzt ist, die die uebergebene Koroutine nur
schliesst statt sie auszufuehren.
"""
import pytest


@pytest.fixture
def role_setup(import_entry_module):
    return import_entry_module("role_setup")


def test_role_page_contains_setup_choices(role_setup):
    assert "Ersteinrichtung" in role_setup.ROLE_PAGE
    assert "gamification" in role_setup.ROLE_PAGE
    assert "gatehill" in role_setup.ROLE_PAGE


@pytest.mark.asyncio
async def test_send_role_page_writes_html(role_setup, fake_writer):
    await role_setup.send_role_page(fake_writer)
    assert "200" in fake_writer.status_line
    assert b"text/html" in fake_writer.response
    assert b"Ersteinrichtung" in fake_writer.response


@pytest.mark.asyncio
async def test_handle_client_serves_role_page_for_default_path(role_setup, make_reader, fake_writer):
    reader = make_reader(b"GET / HTTP/1.1\r\n")
    await role_setup.handle_client(reader, fake_writer)
    assert "200" in fake_writer.status_line
    assert b"Ersteinrichtung" in fake_writer.response


@pytest.mark.asyncio
async def test_handle_client_set_role_rejects_invalid_role(role_setup, make_reader, fake_writer):
    reader = make_reader(b"GET /set-role?role=bogus HTTP/1.1\r\n")
    await role_setup.handle_client(reader, fake_writer)
    assert "400" in fake_writer.status_line
    assert fake_writer.json() == {"ok": False, "error": "Ungueltige Rolle"}


@pytest.mark.asyncio
async def test_handle_client_set_role_missing_role_param(role_setup, make_reader, fake_writer):
    reader = make_reader(b"GET /set-role HTTP/1.1\r\n")
    await role_setup.handle_client(reader, fake_writer)
    assert "400" in fake_writer.status_line


@pytest.mark.asyncio
async def test_handle_client_set_role_success_persists_and_resets(role_setup, make_reader, fake_writer, fast_sleep_ms):
    import machine
    import boot_runtime

    reset_calls = []
    original_reset = machine.reset
    machine.reset = lambda: reset_calls.append(True)
    try:
        reader = make_reader(b"GET /set-role?role=gamification HTTP/1.1\r\n")
        await role_setup.handle_client(reader, fake_writer)
    finally:
        machine.reset = original_reset

    assert "200" in fake_writer.status_line
    assert fake_writer.json() == {"ok": True, "role": "gamification"}
    assert boot_runtime.get_device_role() == "gamification"
    assert reset_calls == [True]


@pytest.mark.asyncio
async def test_handle_client_set_role_gatehill_is_accepted(role_setup, make_reader, fake_writer, fast_sleep_ms):
    import machine

    original_reset = machine.reset
    machine.reset = lambda: None
    try:
        reader = make_reader(b"GET /set-role?role=gatehill HTTP/1.1\r\n")
        await role_setup.handle_client(reader, fake_writer)
    finally:
        machine.reset = original_reset

    assert fake_writer.json() == {"ok": True, "role": "gatehill"}


@pytest.mark.asyncio
async def test_handle_client_closes_writer(role_setup, make_reader, fake_writer):
    reader = make_reader(b"GET / HTTP/1.1\r\n")
    await role_setup.handle_client(reader, fake_writer)
    assert fake_writer.closed is True


@pytest.mark.asyncio
async def test_handle_client_empty_request_returns_without_writing(role_setup, fake_writer):
    from tests.source.conftest import FakeReader

    reader = FakeReader([])  # readline() liefert sofort b"" (Verbindung geschlossen)
    await role_setup.handle_client(reader, fake_writer)
    assert fake_writer.response == b""
