"""Tests fuer tools/license_generator.py (RSA-Schluesselerzeugung/Signierung)."""
import datetime

import pytest

import license_generator as lg


def test_generate_keypair_writes_both_files(tmp_path):
    priv = tmp_path / "private_key.pem"
    pub = tmp_path / "public_key.pem"
    lg.generate_keypair(str(priv), str(pub))
    assert priv.is_file()
    assert pub.is_file()
    assert "PRIVATE KEY" in priv.read_text()
    assert "PUBLIC KEY" in pub.read_text()


def test_load_private_key_roundtrip(tmp_path):
    priv = tmp_path / "private_key.pem"
    pub = tmp_path / "public_key.pem"
    lg.generate_keypair(str(priv), str(pub))
    key = lg.load_private_key(str(priv))
    assert key.key_size == lg.DEFAULT_KEY_SIZE


def test_build_license_payload_format():
    payload = lg.build_license_payload("AABBCCDD11223344", "Max Muster", issued="2024-01-01")
    assert payload == "hardware_id=aabbccdd11223344\ncustomer_id=Max Muster\nissued=2024-01-01\n"


def test_build_license_payload_defaults_issued_to_today():
    payload = lg.build_license_payload("aabbccdd11223344", "Kunde")
    today = datetime.date.today().isoformat()
    assert f"issued={today}" in payload


def test_build_license_payload_rejects_empty_hardware_id():
    with pytest.raises(ValueError):
        lg.build_license_payload("   ", "Kunde")


def test_build_license_payload_strips_newlines_from_customer_id():
    payload = lg.build_license_payload("aabbccdd11223344", "Line1\nLine2\rLine3")
    assert "customer_id=Line1 Line2 Line3" in payload


def test_sign_license_produces_verifiable_signature(tmp_path):
    priv = tmp_path / "private_key.pem"
    pub = tmp_path / "public_key.pem"
    lg.generate_keypair(str(priv), str(pub))
    private_key = lg.load_private_key(str(priv))

    content = lg.sign_license(private_key, "aabbccdd11223344", "Kunde", issued="2024-01-01")
    assert lg.SIGNATURE_MARKER in content
    assert content.startswith("hardware_id=aabbccdd11223344\n")

    import license_verifier

    parsed = license_verifier.parse_license_text(content)
    n, e = license_verifier.load_public_key(str(pub))
    import binascii
    signature = binascii.a2b_base64(parsed["signature_b64"])
    assert license_verifier._rsa_verify_pkcs1_sha256(parsed["signed_payload"], signature, n, e) is True


def test_sign_license_from_key_file(tmp_path):
    priv = tmp_path / "private_key.pem"
    pub = tmp_path / "public_key.pem"
    lg.generate_keypair(str(priv), str(pub))
    content = lg.sign_license_from_key_file(str(priv), "aabbccdd11223344", "Kunde")
    assert "hardware_id=aabbccdd11223344" in content


def test_save_license_writes_unix_newlines(tmp_path):
    path = tmp_path / "license.lic"
    lg.save_license("line1\nline2\n", str(path))
    with open(path, "rb") as f:
        raw = f.read()
    assert b"\r\n" not in raw


def test_self_test_verify_passes_for_valid_license(tmp_path):
    priv = tmp_path / "private_key.pem"
    pub = tmp_path / "public_key.pem"
    lg.generate_keypair(str(priv), str(pub))
    content = lg.sign_license_from_key_file(str(priv), "aabbccdd11223344", "Kunde")
    lg._self_test_verify(content, str(pub), "aabbccdd11223344")  # muss nicht werfen


def test_self_test_verify_fails_for_wrong_hardware_id(tmp_path):
    priv = tmp_path / "private_key.pem"
    pub = tmp_path / "public_key.pem"
    lg.generate_keypair(str(priv), str(pub))
    content = lg.sign_license_from_key_file(str(priv), "aabbccdd11223344", "Kunde")
    with pytest.raises(AssertionError):
        lg._self_test_verify(content, str(pub), "ffffffffffffffff")
