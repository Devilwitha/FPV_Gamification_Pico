"""Treiber fuer ein Grove-kompatibles IR-REC38 Infrarot-Empfangsmodul.

Verkabelung (Grove-Stecker, 3-polig): SIG(OUT) -> GPIO17 (per Konstante
unten aenderbar), VCC -> 3V3, GND -> GND. Der IR-REC38 demoduliert den
38kHz-Traeger bereits selbst (wie ein TSOP-Empfaenger): der Signalpin liegt
im Ruhezustand HIGH und geht LOW, solange ein 38kHz-Signal empfangen wird.

Dekodiert NEC-Frames (Standardprotokoll, siehe ir_emitter.py's Docstring)
komplett per Pin-Interrupt auf fallende Flanken - es wird jeweils nur der
Zeitabstand zur vorherigen fallenden Flanke gemessen und anhand fester
Zeitfenster (Header/Bit-0/Bit-1) klassifiziert. Das ist deutlich robuster
als ein Zaehlen fester Flankenanzahl, weil einzelne verlorene/verrauschte
Frames automatisch verworfen werden (Zustand faellt zurueck auf "warte auf
naechsten Header"), statt den Empfaenger dauerhaft zu verwirren.

poll() liest alle seit dem letzten Aufruf vollstaendig+gueltig (Pruefsumme
ok) empfangenen Frames aus einem kleinen Ringpuffer aus - dafuer wird kurz
machine.disable_irq()/enable_irq() verwendet (Standardmuster fuer
Interrupt-sicheren Zugriff in MicroPython), damit ein Frame nicht waehrend
des Auslesens von einer laufenden ISR ueberschrieben werden kann.
"""

from array import array

import machine
import time

DEFAULT_IR_RECEIVER_PIN = 17
DEFAULT_QUEUE_LEN = 8

# Gleiche NEC-Zeitkonstanten wie ir_emitter.py (absichtlich hier dupliziert
# statt in ein drittes gemeinsames Modul ausgelagert - siehe dortiger
# Docstring zur MicroPython-Kompiliergroesse; die Werte sind stabile
# Protokollkonstanten, keine geteilte Konfiguration).
HEADER_GAP_MIN_US = 10000
HEADER_GAP_MAX_US = 17000
BIT_GAP_MIN_US = 800
BIT_GAP_MID_US = 1700
BIT_GAP_MAX_US = 3000


def _ticks_us():
    try:
        return time.ticks_us()
    except Exception:
        return time.ticks_ms() * 1000


def _ticks_diff(value, reference):
    try:
        return time.ticks_diff(value, reference)
    except Exception:
        return value - reference


class IRReceiver:
    def __init__(self, pin=DEFAULT_IR_RECEIVER_PIN, queue_len=DEFAULT_QUEUE_LEN):
        self.pin_id = pin
        self.available = False
        self.error = ""
        self.checksum_errors = 0

        self._queue_len = queue_len
        self._q_address = array('H', [0] * queue_len)
        self._q_command = array('H', [0] * queue_len)
        self._q_ts_us = array('L', [0] * queue_len)
        self._q_head = 0
        self._q_tail = 0
        self._q_count = 0

        self._collecting = False
        self._bit_count = 0
        self._bit_value = 0
        self._last_edge_us = _ticks_us()

        self._pin = None
        try:
            self._pin = machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP)
            self._pin.irq(trigger=machine.Pin.IRQ_FALLING, handler=self._on_falling_edge)
            self.available = True
        except Exception as e:
            self._pin = None
            self.error = str(e)

    def _on_falling_edge(self, pin):
        now = _ticks_us()
        gap = _ticks_diff(now, self._last_edge_us)
        self._last_edge_us = now

        if HEADER_GAP_MIN_US <= gap <= HEADER_GAP_MAX_US:
            self._collecting = True
            self._bit_count = 0
            self._bit_value = 0
            return

        if not self._collecting:
            return

        if gap < BIT_GAP_MIN_US or gap > BIT_GAP_MAX_US:
            self._collecting = False
            return

        if gap > BIT_GAP_MID_US:
            self._bit_value |= (1 << self._bit_count)
        self._bit_count += 1

        if self._bit_count >= 32:
            self._collecting = False
            self._push_frame(self._bit_value, now)

    def _push_frame(self, bits, now_us):
        address = bits & 0xFF
        address_inv = (bits >> 8) & 0xFF
        command = (bits >> 16) & 0xFF
        command_inv = (bits >> 24) & 0xFF

        if (address ^ address_inv) != 0xFF or (command ^ command_inv) != 0xFF:
            self.checksum_errors += 1
            return

        idx = self._q_head
        self._q_address[idx] = address
        self._q_command[idx] = command
        self._q_ts_us[idx] = now_us & 0xFFFFFFFF
        self._q_head = (idx + 1) % self._queue_len
        if self._q_count < self._queue_len:
            self._q_count += 1
        else:
            # Puffer voll: aeltesten (noch ungelesenen) Eintrag verwerfen,
            # damit neuere Treffer nicht verloren gehen.
            self._q_tail = (self._q_tail + 1) % self._queue_len

    def poll(self):
        """Gibt alle seit dem letzten Aufruf empfangenen, gueltigen Frames
        als Liste von {"address", "command", "ts_us"} zurueck (leer, falls
        keine neuen Frames vorliegen)."""
        frames = []
        while True:
            try:
                irq_state = machine.disable_irq()
            except Exception:
                irq_state = None
            try:
                if self._q_count == 0:
                    break
                idx = self._q_tail
                address = self._q_address[idx]
                command = self._q_command[idx]
                ts_us = self._q_ts_us[idx]
                self._q_tail = (idx + 1) % self._queue_len
                self._q_count -= 1
            finally:
                if irq_state is not None:
                    try:
                        machine.enable_irq(irq_state)
                    except Exception:
                        pass
            frames.append({"address": address, "command": command, "ts_us": ts_us})
        return frames

    def deinit(self):
        if self._pin is not None:
            try:
                self._pin.irq(handler=None)
            except Exception:
                pass
            self._pin = None
        self.available = False
