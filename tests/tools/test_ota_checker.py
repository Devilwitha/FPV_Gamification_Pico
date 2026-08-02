"""Tests fuer tools/ota_checker.py's analyze_file_for_ota() (reine Text-/
Dateigroessenanalyse, kein GUI-Test)."""
import ota_checker


def test_analyze_clean_ascii_file_is_perfect(tmp_path, capsys):
    path = tmp_path / "clean.py"
    path.write_text("print('hello world')\n", encoding="utf-8")
    ota_checker.analyze_file_for_ota(str(path))
    out = capsys.readouterr().out
    assert "PERFEKT FUER OTA GEEIGNET" in out


def test_analyze_file_with_umlauts_warns(tmp_path, capsys):
    path = tmp_path / "umlaut.py"
    path.write_text("# grün und schön\nprint('x')\n", encoding="utf-8")
    ota_checker.analyze_file_for_ota(str(path))
    out = capsys.readouterr().out
    assert "WARNUNG" in out
    assert "BESCHRAENKT FUER OTA GEEIGNET" in out


def test_analyze_file_with_mojibake_is_critical(tmp_path, capsys):
    path = tmp_path / "mojibake.py"
    path.write_text("# fÃƒÆ’Ã¢â‚¬Å¡r Test\nprint('x')\n", encoding="utf-8")
    ota_checker.analyze_file_for_ota(str(path))
    out = capsys.readouterr().out
    assert "NICHT FUER OTA GEEIGNET" in out


def test_analyze_large_file_is_critical(tmp_path, capsys):
    path = tmp_path / "big.py"
    path.write_text("x = 1\n" * 20000, encoding="utf-8")  # deutlich > 45 KB
    ota_checker.analyze_file_for_ota(str(path))
    out = capsys.readouterr().out
    assert "sehr gro" in out
    assert "NICHT FUER OTA GEEIGNET" in out


def test_analyze_medium_file_warns_about_ram(tmp_path, capsys):
    path = tmp_path / "medium.py"
    path.write_text("x = 1\n" * 6000, encoding="utf-8")  # zwischen 25 und 45 KB
    ota_checker.analyze_file_for_ota(str(path))
    out = capsys.readouterr().out
    assert "relativ gro" in out
