"""Tests fuer pico_simulator/pico_runtime.py - die MicroPython-Kompatibilitaets-
schicht selbst, die den source/-Firmware-Tests als Fundament dient.

pico_runtime.install() wurde bereits einmal session-weit vom Root-conftest.py
aufgerufen (siehe dortiger Kommentar) - hier wird daher NICHT erneut
install() aufgerufen (einige interne setattr()-Aufrufe sind per hasattr()
gegen Doppel-Installation abgesichert und wuerden sonst stillschweigend
nichts tun), sondern direkt gegen die bereits global installierten
machine/network/bluetooth-Module sowie pico_runtime's eigene Klassen/
Hilfsfunktionen getestet.
"""
import asyncio
import gc
import time

import pytest

import pico_runtime as pr


# ==================== machine ====================

def test_pin_default_value_is_zero():
    import machine

    pin = machine.Pin(2, machine.Pin.OUT)
    assert pin.value() == 0


def test_pin_set_and_read_value():
    import machine

    pin = machine.Pin(2, machine.Pin.OUT)
    pin.value(1)
    assert pin.value() == 1
    pin.value(0)
    assert pin.value() == 0


def test_pin_value_coerces_truthy_values():
    import machine

    pin = machine.Pin(3, machine.Pin.OUT)
    pin.value(5)
    assert pin.value() == 1


def test_uart_read_empty_when_no_data():
    import machine

    uart = machine.UART(0, baudrate=420000)
    assert uart.any() == 0
    assert uart.read() == b""


def test_uart_read_returns_buffered_bytes_and_clears():
    import machine

    uart = machine.UART(0)
    uart._buffer.extend(b"hello")
    assert uart.any() == 5
    assert uart.read() == b"hello"
    assert uart.any() == 0


def test_uart_read_partial_amount_leaves_remainder():
    import machine

    uart = machine.UART(0)
    uart._buffer.extend(b"hello world")
    assert uart.read(5) == b"hello"
    assert uart.read() == b" world"


def test_wdt_feed_is_noop():
    import machine

    wdt = machine.WDT(timeout=8000)
    wdt.feed()  # darf nicht werfen
    assert wdt.timeout == 8000


def test_machine_reset_does_not_raise(capsys):
    import machine

    machine.reset()
    assert "[SIM]" in capsys.readouterr().out


def test_machine_freq_get_and_set():
    import machine

    machine.freq(150_000_000)
    assert machine.freq() == 150_000_000


def test_machine_unique_id_is_stable_and_matches_constant():
    import machine

    assert machine.unique_id() == pr.SIMULATED_HARDWARE_ID
    assert machine.unique_id() == machine.unique_id()


# ==================== network ====================

def test_wlan_ap_active_toggle_and_config():
    import network

    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    assert ap.active() is True
    ap.config(essid="TestNet")
    assert ap.config("essid") == "TestNet"
    ap.active(False)
    assert ap.active() is False


def test_wlan_ap_ifconfig_roundtrip():
    import network

    ap = network.WLAN(network.AP_IF)
    ap.ifconfig(("10.0.0.1", "255.255.255.0", "10.0.0.1", "10.0.0.1"))
    assert ap.ifconfig() == ("10.0.0.1", "255.255.255.0", "10.0.0.1", "10.0.0.1")


def test_wlan_sta_connect_and_isconnected():
    import network

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    assert sta.isconnected() is False
    sta.connect("HomeNet", "password123")
    assert sta.isconnected() is True
    sta.disconnect()
    assert sta.isconnected() is False


def test_wlan_sta_connect_with_empty_ssid_is_not_connected():
    import network

    sta = network.WLAN(network.STA_IF)
    sta.connect("", "password")
    assert sta.isconnected() is False


def test_wlan_deactivating_sta_clears_connection():
    import network

    sta = network.WLAN(network.STA_IF)
    sta.connect("HomeNet", "password123")
    assert sta.isconnected() is True
    sta.active(False)
    assert sta.isconnected() is False


def test_wlan_scan_returns_empty_list():
    import network

    sta = network.WLAN(network.STA_IF)
    assert sta.scan() == []


# ==================== bluetooth ====================

def test_ble_active_toggle_clears_advertisement_when_disabled():
    import bluetooth

    ble = bluetooth.BLE()
    ble.active(True)
    ble.gap_advertise(1000, adv_data=b"\x01\x02")
    assert ble._advertisement == b"\x01\x02"
    ble.active(False)
    assert ble._advertisement is None


def test_ble_gap_advertise_none_clears_advertisement():
    import bluetooth

    ble = bluetooth.BLE()
    ble.active(True)
    ble.gap_advertise(1000, adv_data=b"\x01")
    ble.gap_advertise(None)
    assert ble._advertisement is None


def test_ble_gap_scan_discovers_other_active_advertisers():
    import bluetooth

    scanner = bluetooth.BLE()
    peer = bluetooth.BLE()
    peer.active(True)
    peer.gap_advertise(1000, adv_data=b"\xaa\xbb")

    events = []
    scanner.irq(lambda event, data: events.append((event, data)))
    scanner.active(True)
    scanner.gap_scan(500, 30000, 30000, False)

    scan_results = [e for e in events if e[0] == 5]
    assert any(data[4] == b"\xaa\xbb" for _event, data in scan_results)
    assert events[-1][0] == 6  # IRQ_SCAN_DONE als letztes Ereignis


def test_ble_gap_scan_excludes_self_and_inactive_peers():
    import bluetooth

    scanner = bluetooth.BLE()
    scanner.active(True)
    scanner.gap_advertise(1000, adv_data=b"\x01")  # eigenes Advertisement

    inactive_peer = bluetooth.BLE()
    inactive_peer.gap_advertise(1000, adv_data=b"\x02")  # nie aktiviert

    events = []
    scanner.irq(lambda event, data: events.append((event, data)))
    scanner.gap_scan(500, 30000, 30000, False)

    scan_results = [data[4] for event, data in events if event == 5]
    assert b"\x01" not in scan_results  # eigenes Signal wird nicht "empfangen"
    assert b"\x02" not in scan_results  # inaktiver Peer sendet nicht


def test_ble_gap_scan_with_none_duration_is_noop():
    import bluetooth

    ble = bluetooth.BLE()
    ble.active(True)
    events = []
    ble.irq(lambda event, data: events.append((event, data)))
    ble.gap_scan(None)
    assert events == []


# ==================== time / gc ====================

def test_ticks_ms_increases_monotonically():
    first = time.ticks_ms()
    second = time.ticks_ms()
    assert second >= first


def test_ticks_diff_computes_signed_difference():
    assert time.ticks_diff(150, 100) == 50
    assert time.ticks_diff(100, 150) == -50


def test_sleep_ms_actually_sleeps_a_short_amount():
    start = time.ticks_ms()
    time.sleep_ms(20)
    elapsed = time.ticks_diff(time.ticks_ms(), start)
    assert elapsed >= 10  # grosszuegige Toleranz gegen Scheduling-Jitter


def test_gc_mem_free_and_alloc_reflect_installed_profile():
    # Werte stammen aus dem session-weiten install()-Aufruf im Root-conftest.py.
    assert isinstance(gc.mem_free(), int)
    assert isinstance(gc.mem_alloc(), int)
    assert gc.mem_free() > 0


# ==================== open() Kompatibilitaet ====================

def test_open_compat_preserves_unix_newlines_for_text_files(tmp_path):
    path = tmp_path / "test.txt"
    with open(path, "w") as f:
        f.write("line1\nline2\n")
    with open(path, "rb") as f:
        raw = f.read()
    assert raw == b"line1\nline2\n"  # keine \r\n-Uebersetzung, wie auf MicroPython


def test_open_compat_leaves_binary_mode_untouched(tmp_path):
    path = tmp_path / "test.bin"
    with open(path, "wb") as f:
        f.write(b"\x00\x01\x02")
    with open(path, "rb") as f:
        assert f.read() == b"\x00\x01\x02"


def test_open_compat_respects_explicit_newline_override(tmp_path):
    path = tmp_path / "test.txt"
    with open(path, "w", newline="\r\n") as f:
        f.write("line1\n")
    with open(path, "rb") as f:
        assert f.read() == b"line1\r\n"


# ==================== asyncio Kompatibilitaet (Ende-zu-Ende) ====================

@pytest.mark.asyncio
async def test_asyncio_start_server_redirects_port_80_and_writer_accepts_str():
    received = bytearray()

    async def handle_client(reader, writer):
        data = await reader.read(100)
        received.extend(data)
        writer.write("pong")  # str statt bytes - write_compat muss das encodieren
        await writer.drain()
        writer.close()

    # start_server_compat leitet Port 80 automatisch auf einen freien
    # localhost-Port um (echte MicroPython-Firmware hoert immer auf 80).
    server = await asyncio.start_server(handle_client, "127.0.0.1", 80)
    try:
        actual_port = server.sockets[0].getsockname()[1]
        assert actual_port != 80

        reader, writer = await asyncio.open_connection("127.0.0.1", actual_port)
        writer.write(b"ping")
        await writer.drain()
        response = await reader.read(100)
        writer.close()

        assert bytes(received) == b"ping"
        assert response == b"pong"
    finally:
        server.close()
        await server.wait_closed()
