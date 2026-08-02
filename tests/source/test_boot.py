"""Tests fuer source/boot.py.

boot.py fuehrt seine gesamte Boot-Entscheidungslogik (Geraetetyp-Erkennung,
AP-Start, Watchdog-Setup, Rollen-/Recovery-Routing) direkt beim Import aus -
es gibt keine separat aufrufbare "main()"-Funktion. Um das ohne echtes
main.py/main_gatehill.py/role_setup.py/recovery.py (gross, hardware-nah, mit
eigenen Endlosschleifen) zu testen, werden diese vier Zielmodule vor jedem
Import durch leere Platzhalter ersetzt (install_stub_module) - boot.py's
`import main` etc. bindet dann nur den bereits im Cache liegenden Platzhalter,
ohne ihn neu auszufuehren. Welcher Zweig durchlaufen wurde, wird ueber die
mit capsys eingefangenen debug_log()-Ausgaben verifiziert.
"""
import os
import types

import boot_runtime


def _stub_all_targets(install_stub_module):
    install_stub_module("main")
    install_stub_module("main_gatehill")
    install_stub_module("recovery")
    install_stub_module("role_setup")
    install_stub_module("main_LilyGo")


def test_lilygo_marker_routes_to_lilygo_firmware(install_stub_module, fresh_import, monkeypatch, capsys):
    _stub_all_targets(install_stub_module)
    monkeypatch.setattr(os, "uname", lambda: types.SimpleNamespace(machine="LILYGO T-QT with ESP32"), raising=False)
    with open("lilygo.device", "w") as f:
        f.write("")

    fresh_import("boot")

    out = capsys.readouterr().out
    assert "LilyGO-Marker erkannt: starte main_LilyGo.py" in out
    # Der LilyGo-Zweig darf NICHT den normalen (Pico-)Hotspot-Start durchlaufen.
    assert "Aktiviere Access Point" not in out


def test_no_device_role_starts_role_setup(install_stub_module, fresh_import, capsys):
    _stub_all_targets(install_stub_module)

    fresh_import("boot")

    out = capsys.readouterr().out
    assert "Keine Geraete-Rolle gewaehlt: starte role_setup.py" in out


def test_gamification_role_with_no_prior_failures_imports_main(install_stub_module, fresh_import, capsys):
    _stub_all_targets(install_stub_module)
    boot_runtime.set_device_role("gamification")

    fresh_import("boot")

    out = capsys.readouterr().out
    assert "direkt vor import main" in out
    assert "role_setup" not in out
    assert "Recovery" not in out

    should_recover, count = boot_runtime.should_boot_recovery()
    assert count == 1
    assert should_recover is False


def test_gatehill_role_is_routed_without_crash(install_stub_module, fresh_import, capsys):
    _stub_all_targets(install_stub_module)
    boot_runtime.set_device_role("gatehill")

    fresh_import("boot")

    out = capsys.readouterr().out
    assert "direkt vor import main" in out
    assert "Keine Geraete-Rolle" not in out
    assert "Wechsle auf recovery.py" not in out


def test_too_many_main_failures_triggers_recovery(install_stub_module, fresh_import, capsys, monkeypatch):
    import time

    _stub_all_targets(install_stub_module)
    boot_runtime.set_device_role("gamification")

    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    for _ in range(boot_runtime.MAX_MAIN_FAILS):
        boot_runtime.mark_main_attempt_failed_or_unhealthy()

    fresh_import("boot")

    out = capsys.readouterr().out
    assert "Wechsle auf recovery.py" in out
    assert "zu viele Main-Fehler" in out


def test_main_retry_flag_forces_main_attempt_despite_fail_lock(install_stub_module, fresh_import, capsys, monkeypatch):
    import time

    _stub_all_targets(install_stub_module)
    boot_runtime.set_device_role("gamification")

    clock = [1_000_000]
    monkeypatch.setattr(time, "ticks_ms", lambda: clock[0])
    for _ in range(boot_runtime.MAX_MAIN_FAILS):
        boot_runtime.mark_main_attempt_failed_or_unhealthy()
    boot_runtime.request_main_retry_once()

    fresh_import("boot")

    out = capsys.readouterr().out
    assert "Recovery-Flag erkannt: main.py wird einmalig erneut versucht." in out
    assert "direkt vor import main" in out
    assert "Wechsle auf recovery.py" not in out

    # Retry-Flag ist verbraucht, Fail-Zaehler wurde vor dem erneuten Versuch
    # geleert und durch den (fingierten) Main-Versuch wieder auf 1 gesetzt.
    assert boot_runtime.consume_main_retry_once() is False
    _should_recover, count = boot_runtime.should_boot_recovery()
    assert count == 1


def test_watchdog_is_registered_on_boot(install_stub_module, fresh_import):
    _stub_all_targets(install_stub_module)
    boot_runtime.set_device_role("gamification")

    boot_module = fresh_import("boot")

    assert boot_runtime._wdt is not None
    boot_runtime.register_wdt(None)
