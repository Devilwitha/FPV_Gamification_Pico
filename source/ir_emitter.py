"""Treiber fuer einen Grove Infrarot-Emitter (IR-LED-Modul).

Verkabelung (Grove-Stecker, 3-polig): SIG -> GPIO16 (per Konstante unten
aenderbar), VCC -> 3V3/5V je nach Grove-Shield, GND -> GND.

Erzeugt einen 38kHz-Traegerton per Hardware-PWM (RP2040 kann das ohne
CPU-Last dauerhaft im Hintergrund erzeugen) und schaltet dessen Tastgrad
(duty) zwischen "an" und "aus", um NEC-kodierte Infrarot-Frames zu senden -
das Standardprotokoll, das die allermeisten IR-Fernbedienungen/-Empfaenger
(inkl. IR-REC38, siehe ir_receiver.py) verstehen. Ein IR-"Schuss" fuer den
Shooter-Spielmodus (siehe shooter_mode.py) ist einfach ein NEC-Frame mit
Adresse=Schuetzen-ID (1 Byte) und Kommando=Schaden (1 Byte).

Blockierend: send() dauert bis zu ~68ms (Laenge eines vollen NEC-Frames) -
das ist auf einem einzelnen "Abfeuern" voellig unproblematisch, siehe
shooter_mode.py's fire().
"""

import machine
import time

DEFAULT_IR_EMITTER_PIN = 16
CARRIER_HZ = 38000
# ~33% Tastgrad ist naeher am NEC-Spezifikationswert als 50% und schont die
# IR-LED etwas - die meisten IR-Empfaenger-ICs (auch der IR-REC38) tolerieren
# aber ohnehin einen breiten Bereich.
CARRIER_DUTY_U16 = 21845

# NEC-Protokoll-Zeiten in Mikrosekunden (Basiseinheit T=562.5us, hier auf
# ganze Mikrosekunden gerundet - unkritisch, IR-Empfaenger tolerieren >=10%
# Jitter problemlos).
NEC_HEADER_MARK_US = 9000
NEC_HEADER_SPACE_US = 4500
NEC_BIT_MARK_US = 562
NEC_ZERO_SPACE_US = 562
NEC_ONE_SPACE_US = 1687


class IRTransmitter:
    def __init__(self, pin=DEFAULT_IR_EMITTER_PIN, carrier_hz=CARRIER_HZ):
        self.pin_id = pin
        self.available = False
        self.error = ""
        self._pwm = None
        try:
            self._pwm = machine.PWM(machine.Pin(pin))
            self._pwm.freq(carrier_hz)
            self._pwm.duty_u16(0)
            self.available = True
        except Exception as e:
            self._pwm = None
            self.error = str(e)

    def _mark(self, microseconds):
        if self._pwm is not None:
            self._pwm.duty_u16(CARRIER_DUTY_U16)
        time.sleep_us(microseconds)

    def _space(self, microseconds):
        if self._pwm is not None:
            self._pwm.duty_u16(0)
        time.sleep_us(microseconds)

    def send(self, address, command):
        """Sendet einen einzelnen vollstaendigen NEC-Frame (address/command
        je 1 Byte, inkl. der vom Protokoll geforderten invertierten
        Pruefbytes). Gibt False zurueck, ohne etwas zu senden, wenn keine
        PWM-Hardware verfuegbar ist (z.B. im Simulator/Unittest)."""
        if not self.available:
            return False

        address &= 0xFF
        command &= 0xFF
        address_inv = (~address) & 0xFF
        command_inv = (~command) & 0xFF

        try:
            self._mark(NEC_HEADER_MARK_US)
            self._space(NEC_HEADER_SPACE_US)
            for byte in (address, address_inv, command, command_inv):
                for bit_index in range(8):
                    bit = (byte >> bit_index) & 1
                    self._mark(NEC_BIT_MARK_US)
                    self._space(NEC_ONE_SPACE_US if bit else NEC_ZERO_SPACE_US)
            self._mark(NEC_BIT_MARK_US)
        finally:
            if self._pwm is not None:
                self._pwm.duty_u16(0)

        return True

    def deinit(self):
        if self._pwm is not None:
            try:
                self._pwm.duty_u16(0)
                self._pwm.deinit()
            except Exception:
                pass
            self._pwm = None
        self.available = False
