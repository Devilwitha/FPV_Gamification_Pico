import mods.shooter.ir_receiver as irr


def test_receiver_available_under_simulator():
    # pico_runtime.py's fake `machine` module now provides Pin.irq/PULL_UP,
    # so the receiver should initialize successfully without real hardware.
    r = irr.IRReceiver()
    assert r.available is True
    assert r.error == ""


def test_poll_returns_empty_list_when_nothing_received():
    r = irr.IRReceiver()
    assert r.poll() == []


def _feed_gaps(monkeypatch, receiver, gaps, start_us=0):
    """Drives receiver._on_falling_edge() as if real falling-edge interrupts
    had arrived with the given inter-edge gaps (in microseconds), by
    monkeypatching the module-level _ticks_us() clock helper."""
    timestamps = []
    t = start_us
    for gap in gaps:
        t += gap
        timestamps.append(t)
    it = iter(timestamps)
    monkeypatch.setattr(irr, "_ticks_us", lambda: next(it))
    receiver._last_edge_us = start_us
    for _ in gaps:
        receiver._on_falling_edge(None)


def _nec_gaps_for(address, command):
    address &= 0xFF
    command &= 0xFF
    address_inv = (~address) & 0xFF
    command_inv = (~command) & 0xFF
    gaps = [13500]  # header mark(9000) + header space(4500)
    for byte in (address, address_inv, command, command_inv):
        for bit_index in range(8):
            bit = (byte >> bit_index) & 1
            gaps.append(2249 if bit else 1124)  # mark(562) + one/zero space
    return gaps


def test_decodes_valid_nec_frame(monkeypatch):
    r = irr.IRReceiver()
    _feed_gaps(monkeypatch, r, _nec_gaps_for(0x12, 0x34))
    frames = r.poll()
    assert len(frames) == 1
    assert frames[0]["address"] == 0x12
    assert frames[0]["command"] == 0x34
    assert r.checksum_errors == 0


def test_rejects_frame_with_bad_checksum(monkeypatch):
    r = irr.IRReceiver()
    # 32 "zero" bits: address=0x00 but its required inverse would need to be
    # 0xFF (it's also 0x00 here) -> checksum mismatch, frame must be dropped.
    gaps = [13500] + [1124] * 32
    _feed_gaps(monkeypatch, r, gaps)
    assert r.poll() == []
    assert r.checksum_errors == 1


def test_noise_before_header_does_not_desync_next_frame(monkeypatch):
    r = irr.IRReceiver()
    # A stray short pulse (not a valid header, not a valid bit gap while not
    # collecting) must simply be ignored, not corrupt the next real frame.
    gaps = [500] + _nec_gaps_for(0x7E, 0x01)
    _feed_gaps(monkeypatch, r, gaps)
    frames = r.poll()
    assert len(frames) == 1
    assert frames[0]["address"] == 0x7E
    assert frames[0]["command"] == 0x01


def test_queue_overwrites_oldest_when_full():
    r = irr.IRReceiver(queue_len=3)
    now = 0
    for address in range(5):
        r._push_frame(_bits_for(address, 1), now)
        now += 1000
    frames = r.poll()
    assert [f["address"] for f in frames] == [2, 3, 4]


def _bits_for(address, command):
    address &= 0xFF
    command &= 0xFF
    address_inv = (~address) & 0xFF
    command_inv = (~command) & 0xFF
    return address | (address_inv << 8) | (command << 16) | (command_inv << 24)


def test_end_to_end_emit_and_decode(monkeypatch):
    """ir_emitter.py's send() and ir_receiver.py's decoder are separate
    modules by design (see ir_receiver.py's docstring) - this proves they
    still agree on the wire format by literally feeding the emitter's
    recorded mark/space trace into the receiver's edge handler."""
    import mods.shooter.ir_emitter as ire

    tx = ire.IRTransmitter()
    trace = []
    monkeypatch.setattr(ire.time, "sleep_us", lambda us: trace.append(us))
    tx.send(0x5A, 0xC3)

    # Falling edges happen at the start of every "mark" (even trace indices).
    fe_times = []
    t = 0
    for i, duration in enumerate(trace):
        if i % 2 == 0:
            fe_times.append(t)
        t += duration
    gaps = [fe_times[i] - fe_times[i - 1] for i in range(1, len(fe_times))]

    rx = irr.IRReceiver()
    _feed_gaps(monkeypatch, rx, gaps, start_us=fe_times[0])

    frames = rx.poll()
    assert len(frames) == 1
    assert frames[0]["address"] == 0x5A
    assert frames[0]["command"] == 0xC3
