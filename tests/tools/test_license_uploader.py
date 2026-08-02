"""Tests fuer tools/license_uploader.py's read_license_fields() (reine
Datei-/Parsing-Logik). find_pico()/upload_license() brauchen einen echten
Pico und werden hier nicht getestet."""
import pytest

import license_uploader as lu


def test_read_license_fields_parses_valid_license(tmp_path):
    content = (
        "hardware_id=aabbccdd11223344\n"
        "customer_id=Kunde\n"
        "issued=2024-01-01\n"
        "---SIGNATURE---\n"
        "c2lnbmF0dXJl\n"
    )
    path = tmp_path / "license.lic"
    path.write_bytes(content.encode("utf-8"))  # exakte Bytes, keine Newline-Uebersetzung

    read_content, fields = lu.read_license_fields(str(path))
    assert read_content == content
    assert fields == {"hardware_id": "aabbccdd11223344", "customer_id": "Kunde", "issued": "2024-01-01"}


def test_read_license_fields_rejects_invalid_format(tmp_path):
    path = tmp_path / "license.lic"
    path.write_text("this is not a license file", encoding="utf-8")
    with pytest.raises(ValueError):
        lu.read_license_fields(str(path))
