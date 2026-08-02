"""Tests fuer source/license_verifier.py.

Nutzt fuer echte Signatur-Rundlauftests tools/license_generator.py (RSA-
Schluesselerzeugung + Signierung) - dieselbe Bibliothek, die auch
webshop/ und pico_simulator/run_firmware.py zum Erzeugen echter Lizenzen
verwenden. So wird nicht nur license_verifier.py's eigene Parsing-/DER-Logik
getestet, sondern der komplette Vertrauenspfad "signiert mit privatem
Schluessel -> vom Pico mit oeffentlichem Schluessel verifiziert".
"""
import binascii
import sys

import pytest

import license_verifier

TOOLS_DIR = str(
    __import__("pathlib").Path(__file__).resolve().parent.parent.parent / "tools"
)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import license_generator  # noqa: E402


@pytest.fixture
def keypair(tmp_path):
    priv = tmp_path / "private_key.pem"
    pub = tmp_path / "public_key.pem"
    license_generator.generate_keypair(str(priv), str(pub))
    return priv, pub


@pytest.fixture
def signed_license(keypair):
    priv, pub = keypair
    hardware_id = "aabbccdd11223344"
    content = license_generator.sign_license_from_key_file(str(priv), hardware_id, "Test Customer")
    return content, pub, hardware_id


def test_verify_valid_license_roundtrip(tmp_path, signed_license):
    content, pub, hardware_id = signed_license
    license_path = tmp_path / "license.lic"
    license_path.write_text(content, newline="\n")

    result = license_verifier.verify(str(license_path), str(pub), expected_hardware_id=hardware_id)
    assert result == "VALID"


def test_verify_valid_license_is_case_insensitive_on_hardware_id(tmp_path, signed_license):
    content, pub, hardware_id = signed_license
    license_path = tmp_path / "license.lic"
    license_path.write_text(content, newline="\n")

    result = license_verifier.verify(str(license_path), str(pub), expected_hardware_id=hardware_id.upper())
    assert result == "VALID"


def test_verify_missing_license_file(tmp_path, keypair):
    _priv, pub = keypair
    result = license_verifier.verify(str(tmp_path / "nope.lic"), str(pub))
    assert result == "MISSING"


def test_verify_wrong_hardware_id_is_invalid(tmp_path, signed_license):
    content, pub, _hardware_id = signed_license
    license_path = tmp_path / "license.lic"
    license_path.write_text(content, newline="\n")

    result = license_verifier.verify(str(license_path), str(pub), expected_hardware_id="ffffffffffffffff")
    assert result == "INVALID"


def test_verify_tampered_payload_is_invalid(tmp_path, signed_license):
    content, pub, hardware_id = signed_license
    tampered = content.replace("Test Customer", "Evil Customer")
    license_path = tmp_path / "license.lic"
    license_path.write_text(tampered, newline="\n")

    result = license_verifier.verify(str(license_path), str(pub), expected_hardware_id=hardware_id)
    assert result == "INVALID"


def test_verify_tampered_signature_is_invalid(tmp_path, signed_license):
    content, pub, hardware_id = signed_license
    marker_idx = content.find(license_verifier.SIGNATURE_MARKER)
    signed_part = content[:marker_idx]
    sig_b64 = content[marker_idx + len(license_verifier.SIGNATURE_MARKER):].strip()
    sig_bytes = bytearray(binascii.a2b_base64(sig_b64))
    sig_bytes[0] ^= 0xFF
    tampered = signed_part + license_verifier.SIGNATURE_MARKER + "\n" + binascii.b2a_base64(bytes(sig_bytes)).decode().strip() + "\n"

    license_path = tmp_path / "license.lic"
    license_path.write_text(tampered, newline="\n")

    result = license_verifier.verify(str(license_path), str(pub), expected_hardware_id=hardware_id)
    assert result == "INVALID"


def test_verify_wrong_public_key_is_invalid(tmp_path, signed_license):
    content, _pub, hardware_id = signed_license
    other_priv = tmp_path / "other_private.pem"
    other_pub = tmp_path / "other_public.pem"
    license_generator.generate_keypair(str(other_priv), str(other_pub))

    license_path = tmp_path / "license.lic"
    license_path.write_text(content, newline="\n")

    result = license_verifier.verify(str(license_path), str(other_pub), expected_hardware_id=hardware_id)
    assert result == "INVALID"


def test_verify_missing_public_key_file_is_invalid(tmp_path, signed_license):
    content, _pub, hardware_id = signed_license
    license_path = tmp_path / "license.lic"
    license_path.write_text(content, newline="\n")

    result = license_verifier.verify(str(license_path), str(tmp_path / "missing_public.pem"), expected_hardware_id=hardware_id)
    assert result == "INVALID"


def test_verify_uses_get_pico_id_when_expected_hardware_id_omitted(tmp_path, keypair, monkeypatch):
    priv, pub = keypair
    hardware_id = "1122334455667788"
    content = license_generator.sign_license_from_key_file(str(priv), hardware_id, "Test Customer")
    license_path = tmp_path / "license.lic"
    license_path.write_text(content, newline="\n")

    monkeypatch.setattr(license_verifier, "get_pico_id", lambda: hardware_id)
    assert license_verifier.verify(str(license_path), str(pub)) == "VALID"

    monkeypatch.setattr(license_verifier, "get_pico_id", lambda: "0000000000000000")
    assert license_verifier.verify(str(license_path), str(pub)) == "INVALID"


class TestParseLicenseText:
    def test_parse_license_text_valid(self):
        text = "hardware_id=abc123\ncustomer_id=Foo\nissued=2024-01-01\n---SIGNATURE---\nc2lnbmF0dXJl\n"
        parsed = license_verifier.parse_license_text(text)
        assert parsed["fields"] == {
            "hardware_id": "abc123",
            "customer_id": "Foo",
            "issued": "2024-01-01",
        }
        assert parsed["signature_b64"] == "c2lnbmF0dXJl"
        assert parsed["signed_payload"] == b"hardware_id=abc123\ncustomer_id=Foo\nissued=2024-01-01\n"

    def test_parse_license_text_missing_marker_returns_none(self):
        assert license_verifier.parse_license_text("hardware_id=abc123\nno marker here") is None

    def test_parse_license_text_missing_hardware_id_returns_none(self):
        text = "customer_id=Foo\n---SIGNATURE---\nc2ln\n"
        assert license_verifier.parse_license_text(text) is None

    def test_parse_license_text_empty_signature_returns_none(self):
        text = "hardware_id=abc123\n---SIGNATURE---\n   \n"
        assert license_verifier.parse_license_text(text) is None

    def test_parse_license_text_ignores_lines_without_equals(self):
        text = "hardware_id=abc123\ngarbage line\ncustomer_id=Foo\n---SIGNATURE---\nc2ln\n"
        parsed = license_verifier.parse_license_text(text)
        assert parsed["fields"] == {"hardware_id": "abc123", "customer_id": "Foo"}


def test_get_pico_id_reads_machine_unique_id():
    import machine

    expected = binascii.hexlify(machine.unique_id()).decode("utf-8")
    assert license_verifier.get_pico_id() == expected
