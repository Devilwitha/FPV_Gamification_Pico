"""Tests fuer tools/license_issuer.py's issue_license() (reine Logik, keine GUI)."""
import json
import os

import pytest


def test_issue_license_fails_without_keypair(license_issuer):
    with pytest.raises(Exception, match="Kein RSA-Schluesselpaar"):
        license_issuer.issue_license("aabbccdd11223344", "Kunde")


def test_issue_license_rejects_empty_hardware_id(license_issuer, build_firmware):
    build_firmware.generate_keypair_if_missing()
    with pytest.raises(ValueError):
        license_issuer.issue_license("   ", "Kunde")


def test_issue_license_success_creates_signed_and_archived_license(license_issuer, build_firmware):
    build_firmware.generate_keypair_if_missing()
    result = license_issuer.issue_license("aabbccdd11223344", "Kunde GmbH")

    assert result["hardware_id"] == "aabbccdd11223344"
    assert os.path.isfile(result["license_record_path"])

    import license_verifier

    with open(result["license_record_path"]) as f:
        content = f.read()
    parsed = license_verifier.parse_license_text(content)
    assert parsed["fields"]["hardware_id"] == "aabbccdd11223344"

    result_status = license_verifier.verify(
        result["license_record_path"], build_firmware.DEFAULT_PUBLIC_KEY_PATH, expected_hardware_id="aabbccdd11223344"
    )
    assert result_status == "VALID"
