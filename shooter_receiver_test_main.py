"""Eigenstaendige Test-Firmware fuer einen zweiten, "nackten" Pico, an den
NUR ein IR-REC38-Empfangsmodul angeschlossen ist (kein Emitter, keine
restliche FPV_Gamification_Pico-Firmware noetig) - dient als schneller
Hardware-Check: kommt ueberhaupt ein sauberes Signal vom Empfaenger an,
bevor man den kompletten Shooter-Modus auf dem echten FPV-Pico testet.

Installation (siehe README.md, Abschnitt "Shooter-Hardware anschliessen
& testen"):
  1. source/ir_receiver.py und DIESE Datei auf den Test-Pico kopieren.
  2. Diese Datei dort in "main.py" umbenennen.
  3. IR-REC38 laut README verkabeln (Standard-Pin: GPIO17).
  4. Pico neu starten.

Verhalten:
  - Board-LED leuchtet dauerhaft, sobald der IR-Empfaenger erfolgreich
    initialisiert wurde ("alles ok").
  - Bei jedem gueltig empfangenen IR-Treffer blinkt die LED kurz aus und
    wieder an; zusaetzlich wird jeder Treffer ueber die serielle
    Konsole (z.B. Thonny) protokolliert.
  - Wurde KEIN Empfaenger erkannt (z.B. falscher Pin, keine Verkabelung),
    blinkt die LED stattdessen dauerhaft schnell als Fehlersignal - sie
    leuchtet nie einfach dauerhaft, wenn tatsaechlich etwas nicht stimmt.
"""
import machine
import time

from ir_receiver import IRReceiver

HIT_BLINK_MS = 150
POLL_INTERVAL_MS = 20
ERROR_BLINK_MS = 80


def _init_led():
    try:
        return machine.Pin("LED", machine.Pin.OUT)
    except Exception:
        pass
    try:
        return machine.Pin(25, machine.Pin.OUT)
    except Exception:
        return None


def run():
    led = _init_led()
    receiver = IRReceiver()

    if not receiver.available:
        print("[SHOOTER-TEST] IR-Empfaenger NICHT verfuegbar:", receiver.error)
        while True:
            if led is not None:
                led.value(1)
                time.sleep_ms(ERROR_BLINK_MS)
                led.value(0)
                time.sleep_ms(ERROR_BLINK_MS)
            else:
                time.sleep_ms(500)

    print("[SHOOTER-TEST] IR-Empfaenger bereit auf Pin", receiver.pin_id, "- warte auf Treffer...")
    if led is not None:
        led.value(1)

    hit_count = 0
    led_off_until_ms = 0

    while True:
        now = time.ticks_ms()

        for frame in receiver.poll():
            hit_count += 1
            print("[SHOOTER-TEST] Treffer #%d: address=%d command=%d" % (hit_count, frame["address"], frame["command"]))
            if led is not None:
                led.value(0)
            led_off_until_ms = time.ticks_add(now, HIT_BLINK_MS)

        if led_off_until_ms and led is not None and time.ticks_diff(led_off_until_ms, now) <= 0:
            led.value(1)
            led_off_until_ms = 0

        time.sleep_ms(POLL_INTERVAL_MS)


run()
