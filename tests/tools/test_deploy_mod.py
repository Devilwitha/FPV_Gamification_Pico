"""Tests fuer tools/deploy_mod.py - CLI zum Uebertragen eines Mod-Ordners
aus source/mods/ auf einen Pico (per mpremote/USB oder webrepl_cli/WLAN).

Nutzt die bestehende build_firmware-Sandbox-Fixture (siehe
tests/tools/conftest.py): source/ wird nach tmp_path kopiert, build_firmware
schreibt/liest nur dort - deploy_mod.py wird ERST NACH diesem Patch frisch
importiert (gleiches Muster wie die license_issuer-Fixture), damit
MODS_SOURCE_DIR auf den temporaeren Ordner zeigt statt auf das echte Repo.
"""
import json
import os
import subprocess

import pytest

# deploy_mod-Fixture ist jetzt in conftest.py definiert (wird auch von
# test_plugin_packager.py genutzt).


def _write_mod(deploy_mod, name, files):
    mod_dir = os.path.join(deploy_mod.MODS_SOURCE_DIR, name)
    os.makedirs(mod_dir, exist_ok=True)
    for filename, content in files.items():
        with open(os.path.join(mod_dir, filename), "w") as f:
            f.write(content)
    return mod_dir


def test_list_local_mods_finds_only_dirs_with_manifest(deploy_mod):
    # Die build_firmware-Sandbox kopiert das gesamte echte source/ (inkl. der
    # bereits vorhandenen mods/example_plugin, mods/shooter) - hier wird nur
    # geprueft, dass ein NEUER Ordner ohne manifest.json nicht mitgezaehlt wird.
    _write_mod(deploy_mod, "with_manifest", {"manifest.json": "{}", "main.py": ""})
    os.makedirs(os.path.join(deploy_mod.MODS_SOURCE_DIR, "without_manifest"), exist_ok=True)

    names = deploy_mod.list_local_mods()
    assert "with_manifest" in names
    assert "without_manifest" not in names


def test_list_local_mods_empty_when_mods_dir_missing(deploy_mod):
    import shutil

    shutil.rmtree(deploy_mod.MODS_SOURCE_DIR, ignore_errors=True)
    assert deploy_mod.list_local_mods() == []


def test_mod_files_lists_flat_files_only(deploy_mod):
    mod_dir = _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": "x = 1"})
    os.makedirs(os.path.join(mod_dir, "subdir"), exist_ok=True)

    assert deploy_mod.list_local_mods()  # sanity: der Fixture-Ordner existiert
    assert deploy_mod._mod_files("demo") == ["main.py", "manifest.json"]


def test_deploy_via_serial_raises_when_mod_empty(deploy_mod):
    with pytest.raises(Exception, match="leer oder existiert nicht"):
        deploy_mod.deploy_via_serial("nonexistent")


def test_deploy_via_serial_auto_detects_port_and_copies_files(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": '{"name": "demo"}', "main.py": "def setup(context):\n    pass\n"})

    monkeypatch.setattr(deploy_mod.build_firmware, "_resolve_mpremote_command", lambda: ["mpremote"])
    monkeypatch.setattr(deploy_mod.build_firmware, "auto_detect_pico_ports", lambda cmd: ["COM7"])

    raw_repl_calls = []
    monkeypatch.setattr(
        deploy_mod.build_firmware, "ensure_device_raw_repl_ready", lambda cmd, port: raw_repl_calls.append(port)
    )

    run_calls = []

    def fake_run_mpremote(mpremote_cmd, args, **kwargs):
        run_calls.append(args)
        return None

    monkeypatch.setattr(deploy_mod.build_firmware, "_run_mpremote", fake_run_mpremote)

    logs = []
    deploy_mod.deploy_via_serial("demo", log=logs.append)

    assert raw_repl_calls == ["COM7"]
    assert ["connect", "COM7", "mkdir", ":mods"] in run_calls
    assert ["connect", "COM7", "mkdir", ":mods/demo"] in run_calls
    assert ["connect", "COM7", "cp", os.path.join(deploy_mod.MODS_SOURCE_DIR, "demo", "main.py"), ":mods/demo/main.py"] in run_calls
    assert ["connect", "COM7", "cp", os.path.join(deploy_mod.MODS_SOURCE_DIR, "demo", "manifest.json"), ":mods/demo/manifest.json"] in run_calls
    assert any("erfolgreich uebertragen" in message for message in logs)


def test_deploy_via_serial_uses_explicit_port_without_auto_detect(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    monkeypatch.setattr(deploy_mod.build_firmware, "_resolve_mpremote_command", lambda: ["mpremote"])

    auto_detect_calls = []
    monkeypatch.setattr(
        deploy_mod.build_firmware, "auto_detect_pico_ports", lambda cmd: auto_detect_calls.append(1) or ["COM99"]
    )
    monkeypatch.setattr(deploy_mod.build_firmware, "ensure_device_raw_repl_ready", lambda cmd, port: None)
    monkeypatch.setattr(deploy_mod.build_firmware, "_run_mpremote", lambda cmd, args, **kw: None)

    deploy_mod.deploy_via_serial("demo", port="COM3", log=lambda *_a: None)

    assert auto_detect_calls == []  # explizit angegebener Port -> keine Auto-Erkennung noetig


def test_deploy_via_serial_raises_when_no_port_found(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    monkeypatch.setattr(deploy_mod.build_firmware, "_resolve_mpremote_command", lambda: ["mpremote"])
    monkeypatch.setattr(deploy_mod.build_firmware, "auto_detect_pico_ports", lambda cmd: [])

    with pytest.raises(Exception, match="Kein Pico-COM-Port gefunden"):
        deploy_mod.deploy_via_serial("demo")


def test_deploy_via_serial_ignores_mkdir_failure_as_best_effort(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    monkeypatch.setattr(deploy_mod.build_firmware, "_resolve_mpremote_command", lambda: ["mpremote"])
    monkeypatch.setattr(deploy_mod.build_firmware, "ensure_device_raw_repl_ready", lambda cmd, port: None)

    def fake_run_mpremote(cmd, args, **kwargs):
        if "mkdir" in args:
            raise Exception("EEXIST")
        return None

    monkeypatch.setattr(deploy_mod.build_firmware, "_run_mpremote", fake_run_mpremote)

    # Darf trotz mkdir-Fehler nicht crashen (best effort - Ordner existiert
    # vermutlich schon von einem frueheren Deploy).
    deploy_mod.deploy_via_serial("demo", port="COM3", log=lambda *_a: None)


def test_deploy_via_wifi_raises_when_webrepl_cli_missing(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: None)

    with pytest.raises(Exception, match="webrepl_cli wurde nicht"):
        deploy_mod.deploy_via_wifi("demo", "192.168.4.1", "secret")


def test_deploy_via_wifi_calls_webrepl_cli_per_file(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": '{"name": "demo"}', "main.py": "x = 1"})
    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: "/usr/bin/webrepl_cli" if name == "webrepl_cli" else None)

    calls = []

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(deploy_mod.subprocess, "run", fake_run)

    logs = []
    deploy_mod.deploy_via_wifi("demo", "192.168.4.1", "secret", log=logs.append)

    assert len(calls) == 2
    for cmd in calls:
        assert cmd[0] == "/usr/bin/webrepl_cli"
        assert cmd[1:3] == ["-p", "secret"]
        assert cmd[3].endswith(("main.py", "manifest.json"))
        assert cmd[4].startswith("192.168.4.1:mods/demo/")
    assert any("erfolgreich per WLAN uebertragen" in message for message in logs)


def test_deploy_via_wifi_raises_with_helpful_message_on_failure(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    monkeypatch.setattr(deploy_mod.shutil, "which", lambda name: "/usr/bin/webrepl_cli")

    def fake_run(cmd, capture_output, text, timeout):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "connection refused"

        return Result()

    monkeypatch.setattr(deploy_mod.subprocess, "run", fake_run)

    with pytest.raises(Exception, match="connection refused"):
        deploy_mod.deploy_via_wifi("demo", "192.168.4.1", "secret")


def test_main_dispatches_to_serial_mode(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    calls = []
    monkeypatch.setattr(deploy_mod, "deploy_via_serial", lambda mod_name, port=None, log=print: calls.append((mod_name, port)))

    exit_code = deploy_mod.main(["--mod", "demo", "--mode", "serial", "--port", "COM4"])

    assert exit_code == 0
    assert calls == [("demo", "COM4")]


def test_main_dispatches_to_wifi_mode_with_explicit_args(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    calls = []
    monkeypatch.setattr(
        deploy_mod, "deploy_via_wifi", lambda mod_name, host, password, log=print: calls.append((mod_name, host, password))
    )

    exit_code = deploy_mod.main(["--mod", "demo", "--mode", "wifi", "--host", "192.168.4.1", "--password", "secret"])

    assert exit_code == 0
    assert calls == [("demo", "192.168.4.1", "secret")]


def test_main_returns_error_for_unknown_mod(deploy_mod, capsys):
    exit_code = deploy_mod.main(["--mod", "does-not-exist", "--mode", "serial"])
    assert exit_code == 1
    assert "nicht gefunden" in capsys.readouterr().err


def test_main_falls_back_to_interactive_menu_when_mod_missing(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    monkeypatch.setattr(deploy_mod, "_interactive_menu", lambda: ("demo", "serial"))

    calls = []
    monkeypatch.setattr(deploy_mod, "deploy_via_serial", lambda mod_name, port=None, log=print: calls.append(mod_name))

    exit_code = deploy_mod.main([])

    assert exit_code == 0
    assert calls == ["demo"]


def test_main_wifi_mode_prompts_for_missing_host_and_password(deploy_mod, monkeypatch):
    _write_mod(deploy_mod, "demo", {"manifest.json": "{}", "main.py": ""})
    inputs = iter(["192.168.4.1", "secret"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(inputs))

    calls = []
    monkeypatch.setattr(
        deploy_mod, "deploy_via_wifi", lambda mod_name, host, password, log=print: calls.append((mod_name, host, password))
    )

    exit_code = deploy_mod.main(["--mod", "demo", "--mode", "wifi"])

    assert exit_code == 0
    assert calls == [("demo", "192.168.4.1", "secret")]


def test_prompt_choice_retries_on_invalid_input(deploy_mod, monkeypatch, capsys):
    inputs = iter(["abc", "99", "2"])
    monkeypatch.setattr("builtins.input", lambda *_a: next(inputs))

    result = deploy_mod._prompt_choice(["serial", "wifi"], "Modus waehlen")

    assert result == "wifi"
    assert "Ungueltige Auswahl" in capsys.readouterr().out
