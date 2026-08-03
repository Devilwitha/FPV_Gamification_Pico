import mods.shooter.ir_emitter as ire


def test_transmitter_available_under_simulator():
    # pico_runtime.py's fake `machine` module provides a PWM stub, so the
    # transmitter should initialize successfully even without real hardware.
    tx = ire.IRTransmitter()
    assert tx.available is True
    assert tx.error == ""


def test_send_configures_carrier_frequency():
    tx = ire.IRTransmitter()
    tx.send(0x01, 0x02)
    assert tx._pwm.freq() == ire.CARRIER_HZ


def test_send_leaves_carrier_off_after_frame():
    tx = ire.IRTransmitter()
    tx.send(0x01, 0x02)
    assert tx._pwm.duty_u16() == 0


def test_send_returns_true_on_success():
    tx = ire.IRTransmitter()
    assert tx.send(0xAB, 0xCD) is True


def test_send_returns_false_when_unavailable():
    tx = ire.IRTransmitter()
    tx.available = False
    assert tx.send(0x01, 0x02) is False


def test_deinit_marks_unavailable():
    tx = ire.IRTransmitter()
    tx.deinit()
    assert tx.available is False
    assert tx._pwm is None


def test_send_emits_exact_nec_timing_sequence(monkeypatch):
    """Regression test for the hand-rolled NEC bit-banging in send(): records
    every (duty, duration) pair passed to time.sleep_us() and compares it
    against the frame independently reconstructed from address/command using
    the NEC spec (header, 32 bits LSB-first per byte, final burst)."""
    tx = ire.IRTransmitter()
    recorded = []

    def fake_sleep_us(us):
        recorded.append((tx._pwm.duty_u16(), us))

    monkeypatch.setattr(ire.time, "sleep_us", fake_sleep_us)

    address, command = 0xB0, 0x0F  # mixes 0/1 bits in both bytes
    tx.send(address, command)

    address_inv = (~address) & 0xFF
    command_inv = (~command) & 0xFF
    expected = [
        (ire.CARRIER_DUTY_U16, ire.NEC_HEADER_MARK_US),
        (0, ire.NEC_HEADER_SPACE_US),
    ]
    for byte in (address, address_inv, command, command_inv):
        for bit_index in range(8):
            bit = (byte >> bit_index) & 1
            expected.append((ire.CARRIER_DUTY_U16, ire.NEC_BIT_MARK_US))
            expected.append((0, ire.NEC_ONE_SPACE_US if bit else ire.NEC_ZERO_SPACE_US))
    expected.append((ire.CARRIER_DUTY_U16, ire.NEC_BIT_MARK_US))

    assert recorded == expected
